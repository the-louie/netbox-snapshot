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

        try:
            nk = resolve(registry, content_type, record, parent_lookup=parent_lookup)
        except (KeyError, ValueError):
            # Cannot compute NK; skip the record and surface a flag.
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

        body = _apply_allowlist(record, allowlist)
        body = _rewrite_fks(content_type, body, openapi, registry, parent_lookup)
        yield ExtractedRow(content_type=content_type, natural_key=nk, body=body), None


def _apply_allowlist(
    record: Mapping[str, Any], allowlist: frozenset[str]
) -> dict[str, Any]:
    """Keep only the fields the destination will accept."""
    return {k: v for k, v in record.items() if k in allowlist}


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
            rewritten[field_name] = rewrite_simple_fk(
                value, paired_type, registry, parent_lookup
            )
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
                rewrite_polymorphic(item, registry, parent_lookup)
                for item in value
                if isinstance(item, Mapping)
            ]
            continue

        if spec.fk_target is None:
            rewritten[field_name] = value
            continue

        # Category 2: m2m list of simple FK values.
        if spec.is_m2m:
            rewritten[field_name] = rewrite_m2m(value, spec.fk_target, registry, parent_lookup)
            continue

        # Category 1: simple FK.
        rewritten[field_name] = rewrite_simple_fk(
            value, spec.fk_target, registry, parent_lookup
        )
    return rewritten


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
