"""Per-endpoint extractor (FEAT-11a/b/c/d).

The extractor is the workhorse of the export engine. Given an
endpoint, an OpenAPI schema, a natural-key registry, and a
parent-lookup map, it walks every record and emits a sequence of
snapshot rows (one dict per record).

Pipeline per record:

1. **Allowlist filter** (FEAT-11b). Drop fields that are not in
   the `write_allowlist` for the content type so the snapshot
   does not carry read-only data the importer will ignore.
2. **FK rewrite** (FEAT-11c via fk_rewrite.py). Replace every
   FK id with the natural-key tuple from the registry.
3. **Install-local classifier** (FEAT-11d via install_local.py).
   Drop records flagged as install-local and write a Flag entry
   to the flag log.

The extractor does not write to disk. The writer (FEAT-14a) takes
the yielded dicts and lays them out as JSONL.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from nbsnap.export.fk_rewrite import (
    ParentLookup,
    rewrite_m2m,
    rewrite_polymorphic,
    rewrite_simple_fk,
)
from nbsnap.export.install_local import Flag, is_install_local
from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.resolver import resolve
from nbsnap.schema.openapi import OpenAPI


@dataclass(frozen=True)
class ExtractedRow:
    """One record after the extractor's three-step transform."""

    content_type: str
    natural_key: tuple[Any, ...]
    body: dict[str, Any]


def extract(
    content_type: str,
    records: Iterator[Mapping[str, Any]],
    *,
    openapi: OpenAPI,
    registry: NKRegistry,
    parent_lookup: ParentLookup,
    source_url: str,
) -> Iterator[tuple[ExtractedRow | None, Flag | None]]:
    """Yield `(extracted_row | None, flag | None)` per source record.

    `extracted_row` is `None` when the record is install-local;
    `flag` is the audit entry in that case. Both being non-None at
    once would be a bug, the caller asserts at most one is set.
    """

    allowlist = openapi.write_allowlist(content_type)

    for record in records:
        flag = is_install_local(content_type, record, source_url)
        if flag is not None:
            yield None, flag
            continue

        body = _apply_allowlist(record, allowlist)
        body = _rewrite_fks(content_type, body, openapi, registry, parent_lookup)

        # Compute the natural key from the REWRITTEN body so any FK
        # fields the NKSpec references (e.g. IPAddress's NK has
        # assigned_object_id) carry the destination-resolvable NK
        # tuple instead of the source's numeric id. Resolving from
        # the raw record would leak source ids into NKs and break
        # destination lookups, because the destination's interface
        # ids do not match the source's.
        try:
            nk = resolve(registry, content_type, body, parent_lookup=parent_lookup)
        except (KeyError, ValueError):
            yield (
                None,
                Flag(
                    content_type=content_type,
                    natural_key=(),
                    field="natural_key",
                    reason="missing or empty NK field",
                ),
            )
            continue

        yield ExtractedRow(content_type=content_type, natural_key=nk, body=body), None


# NetBox 4.x serialises every enum field with a `{value, label}`
# wrapper on the GET response, but the matching POST/PATCH endpoint
# accepts only the bare value string. This set is the exact key
# shape we collapse; checking for equality (not subset) guards
# against accidentally collapsing a payload dict that happens to
# carry a `value` field alongside other keys.
_ENUM_DICT_KEYS = frozenset({"value", "label"})


def _collapse_enum_dict(value: Any) -> Any:
    """Return the `value` slot when `value` is a NetBox enum-dict.

    NetBox returns choice fields like `status` as
    `{"value": "active", "label": "Active"}` when you read them
    via GET. The same field on POST/PATCH must be the bare string
    `"active"`. Without this collapse the import side gets

        HTTP 400 {"status": ["Value must be passed directly..."]}

    on every record that carries a choice field, which in
    practice means every Site, Device, IPAddress, Prefix, etc.

    We only collapse when the dict has EXACTLY the two keys we
    expect. Any extra key suggests a real payload dict rather
    than the enum wrapper, so we leave it alone.
    """

    if isinstance(value, Mapping) and frozenset(value.keys()) == _ENUM_DICT_KEYS:
        inner = value["value"]
        # NetBox's enum values are strings in every observed
        # case, but we allow int / bool / None for safety so a
        # future choice type does not silently get mangled. The
        # `X | Y` union syntax requires Python 3.10+ and the
        # project floor is 3.11 (see pyproject.toml).
        if isinstance(inner, str | int | bool) or inner is None:
            return inner
    return value


def _apply_allowlist(
    record: Mapping[str, Any], allowlist: frozenset[str]
) -> dict[str, Any]:
    """Keep only the fields the destination will accept.

    Two transforms happen at this boundary:

    1. Field allowlist filter: drop fields NetBox does not
       accept on POST/PATCH (e.g. `id`, `url`, `display`,
       `created`, `last_updated`). The allowlist comes from
       the OpenAPI request-body schema.
    2. Enum-dict collapse: NetBox sends choice values as
       `{value, label}` dicts on GET but requires the bare
       value on write, see `_collapse_enum_dict`.
    """
    return {k: _collapse_enum_dict(v) for k, v in record.items() if k in allowlist}


def _rewrite_fks(
    content_type: str,
    body: dict[str, Any],
    openapi: OpenAPI,
    registry: NKRegistry,
    parent_lookup: ParentLookup,
) -> dict[str, Any]:
    """Walk the record, rewrite every FK in place.

    Three categories of FK get separate handling:

    1. **Simple FK** (single id or nested dict): `device`, `site`,
       `primary_ip4`. Resolved against the registered NK strategy
       for the FK target content type.
    2. **M2M** (list of FK values): `tags`. Each item resolved.
    3. **Polymorphic generic FK**, which surfaces in two shapes in
       NetBox 4.x:
       a. **Paired fields** (`assigned_object_type` + `assigned_object_id`
          on IPAddress, Service, etc.): the type field names the
          target content type, the id field needs to be rewritten
          against that type.
       b. **List of polymorphic refs** (`a_terminations`,
          `b_terminations` on Cable): each list item has
          `object_type`, `object_id`, and a nested `object`. Each
          item is rewritten using the type field as the target.
    """
    rewritten: dict[str, Any] = {}

    # Detect generic FK pairs once up front so the loop below can
    # consult the type field for each id field.
    paired = _detect_polymorphic_pairs(body)

    for field_name, value in body.items():
        # Category 3a: paired generic FK, the id half.
        paired_type = paired.get(field_name)
        if paired_type is not None:
            # NetBox includes a sibling field with the nested
            # representation: for `assigned_object_id` look at
            # `assigned_object`, for `<prefix>_object_id` look at
            # `<prefix>_object`. Use the nested dict directly so
            # we do not depend on parent_lookup ordering.
            nested = _paired_nested_value(body, field_name)
            try:
                rewritten[field_name] = _rewrite_with_fallback(
                    value, nested, paired_type, registry, parent_lookup
                )
            except (KeyError, ValueError) as exc:
                _warn_dropped(content_type, field_name, paired_type, exc)
            continue

        spec = openapi.field_spec(content_type, field_name)

        # Category 3b: list of polymorphic refs (cable terminations).
        if (
            spec.is_m2m
            and isinstance(value, list)
            and value
            and isinstance(value[0], Mapping)
            and "object_type" in value[0]
            and "object_id" in value[0]
        ):
            rewritten[field_name] = [
                _safe_polymorphic_item(item, registry, parent_lookup, content_type, field_name)
                for item in value
                if isinstance(item, Mapping)
            ]
            continue

        if spec.fk_target is None:
            rewritten[field_name] = value
            continue

        # Category 2: m2m list of simple FK values.
        if spec.is_m2m:
            try:
                rewritten[field_name] = rewrite_m2m(
                    value, spec.fk_target, registry, parent_lookup
                )
            except (KeyError, ValueError) as exc:
                _warn_dropped(content_type, field_name, spec.fk_target, exc)
            continue

        # Category 1: simple FK.
        try:
            rewritten[field_name] = rewrite_simple_fk(
                value, spec.fk_target, registry, parent_lookup
            )
        except (KeyError, ValueError) as exc:
            _warn_dropped(content_type, field_name, spec.fk_target, exc)
    return rewritten


def _paired_nested_value(body: Mapping[str, Any], id_field: str) -> Mapping[str, Any] | None:
    """Return the sibling nested-record field for `<prefix>_object_id`.

    For `assigned_object_id` look up `assigned_object`. NetBox 4.x
    includes both shapes in the GET response: the bare id pair and
    the full nested representation. Using the nested representation
    avoids depending on parent_lookup being populated for the
    target content type, which matters when the parent type comes
    later in the topo order.
    """

    if not id_field.endswith("_id"):
        return None
    base = id_field[: -len("_id")]
    nested = body.get(base)
    return nested if isinstance(nested, Mapping) else None


def _rewrite_with_fallback(
    value: Any,
    nested: Mapping[str, Any] | None,
    target_ct: str,
    registry: NKRegistry,
    parent_lookup: ParentLookup,
) -> Any:
    """Try the nested dict first (no parent_lookup dependency), then
    fall back to the bare-id path.
    """

    if nested is not None:
        return rewrite_simple_fk(nested, target_ct, registry, parent_lookup)
    return rewrite_simple_fk(value, target_ct, registry, parent_lookup)


# Module-level sentinel so the "we already warned about this" check
# is process-wide. The warning is informational and noisy when a
# whole export hits the same unregistered content type on every row.
_WARNED_UNREGISTERED: set[tuple[str, str, str]] = set()


def _warn_dropped(
    content_type: str, field_name: str, fk_target: str, exc: Exception
) -> None:
    """Log once per (ct, field, target) triple and drop the FK field."""

    import logging

    key = (content_type, field_name, fk_target)
    if key in _WARNED_UNREGISTERED:
        return
    _WARNED_UNREGISTERED.add(key)
    logger = logging.getLogger(__name__)
    logger.warning(
        "dropping FK %s.%s -> %s, no NKSpec registered (%s)",
        content_type,
        field_name,
        fk_target,
        exc.args[0] if exc.args else str(exc),
    )


def _safe_polymorphic_item(
    item: Mapping[str, Any],
    registry: NKRegistry,
    parent_lookup: ParentLookup,
    content_type: str,
    field_name: str,
) -> dict[str, Any] | None:
    """Run rewrite_polymorphic, drop the item (warn) on a soft error."""

    try:
        return rewrite_polymorphic(item, registry, parent_lookup)
    except (KeyError, ValueError) as exc:
        target = str(item.get("object_type") or "?")
        _warn_dropped(content_type, field_name, target, exc)
        return None


def _detect_polymorphic_pairs(body: Mapping[str, Any]) -> dict[str, str]:
    """Find `<prefix>_object_type` + `<prefix>_object_id` pairs.

    Returns `{id_field_name: content_type_value}` so the rewriter
    can pick the right NK target for each id field.
    """
    pairs: dict[str, str] = {}
    for key, value in body.items():
        if not key.endswith("_type"):
            continue
        prefix = key[: -len("_type")]
        id_field = f"{prefix}_id"
        if id_field in body and isinstance(value, str) and "." in value:
            pairs[id_field] = value
    return pairs
