"""Natural-key resolvers (FEAT-09a/b/c).

A *resolver* takes one record (the dict NetBox returned) plus the
NK registry and returns a "natural-key tuple". The tuple is the
form persisted in the snapshot file: an importer can re-look-up
the destination's local id from the same tuple.

Three strategies are wired:

* `resolve_slug`: read a single field, surface it as a one-element
  tuple. Used by Sites, Roles, Tags.
* `resolve_composite`: read every field in `NKSpec.fields`. For
  parent FK fields, recurse into the parent's record so the
  parent's natural key replaces the FK id.
* `resolve_polymorphic_set`: build an unordered set of
  `(content_type, NK)` pairs. Used by Cable terminations.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nbsnap.natkey.model import NKField, NKRegistry, NKSpec, Strategy

# A `NaturalKey` is a tuple of primitives that uniquely identifies
# one record. The empty tuple is the marker for "this content type
# uses a polymorphic NK and resolve_polymorphic_set should be used".
NaturalKey = tuple[Any, ...]


class ResolverFieldError(ValueError):
    """Raised when a natural-key resolver cannot read a required field.

    ARCH-09a. The legacy raise was a bare :class:`ValueError` whose
    only context lived in its message string. The audit's complaint:
    when 5 000 rows fail with the same shape, the operator wants the
    failure anchored to the offending ``(content_type, natural_key,
    field)`` tuple so a grep against ``audit.jsonl`` finds them. The
    new exception carries that anchor on the instance:

    * ``content_type``  : the record's NetBox content type
      ("dcim.device", "ipam.iprange", ...).
    * ``natural_key``   : the partial NK that was being built, or
      None when the failure happened before any field landed.
    * ``field_name``    : the field the resolver could not read.
    * ``hint``          : a short operator-facing hint at one of the
      three likely causes (missing source data, scope mismatch,
      schema skew). Free-form string, no enum, so a new hint can
      land at a raise site without a wider patch.

    The exception inherits :class:`ValueError` so any pre-ARCH-09a
    ``except ValueError`` clause still catches it, and the
    :meth:`__str__` renders a single-line summary suited for
    audit-row inclusion.
    """

    def __init__(
        self,
        message: str,
        *,
        content_type: str,
        natural_key: tuple[Any, ...] | None,
        field_name: str,
        hint: str,
    ) -> None:
        self.content_type = content_type
        self.natural_key = natural_key
        self.field_name = field_name
        self.hint = hint
        self._message = message
        super().__init__(self._render())

    def _render(self) -> str:
        # Format: "[ct nk.field] message (hint: hint)". The square
        # brackets are easy to spot in a wall of audit lines.
        return (
            f"[{self.content_type} {self.natural_key}.{self.field_name}] "
            f"{self._message} (hint: {self.hint})"
        )


def resolve(
    registry: NKRegistry,
    content_type: str,
    record: Mapping[str, Any],
    parent_lookup: Mapping[tuple[str, int], Mapping[str, Any]] | None = None,
) -> NaturalKey:
    """Dispatch to the strategy-specific resolver.

    `parent_lookup` is an optional map `(content_type, id) -> record`
    that the composite resolver consults when recursing into a
    parent FK. The exporter builds this map once per content type;
    the importer does not need it because it works the other way.
    """

    spec = registry.get(content_type)
    if spec.strategy is Strategy.SLUG:
        return resolve_slug(spec, record)
    if spec.strategy is Strategy.COMPOSITE:
        return resolve_composite(registry, spec, record, parent_lookup)
    return resolve_polymorphic_set(spec, record, registry)


def resolve_slug(spec: NKSpec, record: Mapping[str, Any]) -> NaturalKey:
    """Single-field NK. Read the field, wrap in a tuple."""

    if len(spec.fields) != 1:
        msg = f"slug NK for {spec.content_type} must have exactly one field"
        raise ValueError(msg)
    field_name = spec.fields[0].name
    value = record.get(field_name)
    if value in (None, ""):
        # ARCH-09c: ResolverFieldError lets the audit log anchor the
        # row by content type + field. natural_key is None because the
        # NK has not been assembled yet, the failure happened on its
        # one and only field. Hint targets the three likely causes,
        # an empty slug is almost always a source-side data issue.
        raise ResolverFieldError(
            f"slug NK field {field_name!r} is empty",
            content_type=spec.content_type,
            natural_key=None,
            field_name=field_name,
            hint="missing source data",
        )
    return (value,)


def resolve_composite(
    registry: NKRegistry,
    spec: NKSpec,
    record: Mapping[str, Any],
    parent_lookup: Mapping[tuple[str, int], Mapping[str, Any]] | None,
) -> NaturalKey:
    """Composite NK. Read each field; recurse for parent FK fields.

    Polymorphic pair handling: NetBox stores generic FKs as paired
    fields, `<prefix>_object_type` (the content type) and
    `<prefix>_object_id` (the id on that content type). When the
    NKSpec includes the `_id` half (e.g. ipam.ipaddress NK uses
    assigned_object_id) the raw int alone is not portable across
    NetBox installs; we substitute the target's natural key by
    consulting the `_type` sibling and the nested
    `<prefix>_object` representation if available.
    """

    parts: list[Any] = []
    for field in spec.fields:
        value = record.get(field.name)
        if field.parent_content_type is not None:
            parts.append(_resolve_parent(field, value, registry, parent_lookup))
        elif _looks_like_polymorphic_id(field.name, record):
            parts.append(_resolve_polymorphic_id_in_nk(field.name, record, registry, parent_lookup))
        else:
            parts.append(value)
    return tuple(parts)


def _looks_like_polymorphic_id(field_name: str, record: Mapping[str, Any]) -> bool:
    """True iff `field_name` is `<prefix>_object_id` and a matching
    `<prefix>_object_type` field exists on the record carrying a
    content-type string.
    """
    if not field_name.endswith("_object_id"):
        return False
    type_field = field_name[: -len("_id")] + "_type"
    return isinstance(record.get(type_field), str) and "." in record.get(type_field, "")


def _resolve_polymorphic_id_in_nk(
    field_name: str,
    record: Mapping[str, Any],
    registry: NKRegistry,
    parent_lookup: Mapping[tuple[str, int], Mapping[str, Any]] | None,
) -> Any:
    """Compute the NK of a polymorphic FK target from the
    `<prefix>_object` nested representation, the `<prefix>_object_type`
    name, and the bare id.

    Returns the NK tuple of the target record, or the bare id when
    the target's content type has no NKSpec or its record is not
    accessible.
    """
    prefix = field_name[: -len("_id")]  # `<prefix>_object`
    type_field = prefix + "_type"
    nested_field = prefix  # `<prefix>_object`
    target_ct = record.get(type_field)
    if not isinstance(target_ct, str) or not registry.has(target_ct):
        return record.get(field_name)
    nested = record.get(nested_field)
    if isinstance(nested, Mapping):
        try:
            return resolve(registry, target_ct, nested, parent_lookup=parent_lookup)
        except (KeyError, ValueError):
            pass
    bare_id = record.get(field_name)
    if isinstance(bare_id, int) and parent_lookup is not None:
        full = parent_lookup.get((target_ct, bare_id))
        if full is not None:
            try:
                return resolve(registry, target_ct, full, parent_lookup=parent_lookup)
            except (KeyError, ValueError):
                pass
    return bare_id


def _resolve_parent(
    field: NKField,
    raw: Any,
    registry: NKRegistry,
    parent_lookup: Mapping[tuple[str, int], Mapping[str, Any]] | None,
) -> Any:
    """Recurse into a parent FK to substitute its NK for the FK id."""

    # NetBox 4.x serialises a FK as either a `{"id": int, ...}` dict
    # (nested representation) or as a bare int (brief representation).
    # We support both shapes so the same resolver works against
    # different views of the API.
    if isinstance(raw, Mapping):
        parent_id = raw.get("id")
        # The nested representation already carries enough fields
        # for resolution if its NKSpec is shallow.
        if not isinstance(parent_id, int):
            return raw
        # Prefer the parent_lookup map when supplied so we get the
        # full record, not just the brief.
        if parent_lookup is not None and field.parent_content_type is not None:
            lookup_key = (field.parent_content_type, parent_id)
            parent_record = parent_lookup.get(lookup_key)
            if parent_record is not None:
                return resolve(
                    registry,
                    field.parent_content_type,
                    parent_record,
                    parent_lookup,
                )
        # Fall back: resolve the nested representation itself.
        if field.parent_content_type is None:
            return raw
        return resolve(registry, field.parent_content_type, raw, parent_lookup)
    if isinstance(raw, int) and field.parent_content_type is not None:
        if parent_lookup is None:
            # ARCH-09c: a missing parent_lookup is almost always
            # scope mismatch, the exporter excluded the parent
            # content type so the FK has nothing to substitute.
            raise ResolverFieldError(
                f"cannot resolve {field.parent_content_type} id {raw} without a parent_lookup map",
                content_type=field.parent_content_type,
                natural_key=None,
                field_name=field.name,
                hint="scope mismatch",
            )
        lookup_key = (field.parent_content_type, raw)
        parent_record = parent_lookup.get(lookup_key)
        if parent_record is None:
            # ARCH-09c: same scope-mismatch story, but specifically
            # that the parent_lookup map was built without this id.
            raise ResolverFieldError(
                f"parent_lookup is missing {field.parent_content_type} id {raw}",
                content_type=field.parent_content_type,
                natural_key=None,
                field_name=field.name,
                hint="scope mismatch",
            )
        return resolve(registry, field.parent_content_type, parent_record, parent_lookup)
    return raw


def resolve_polymorphic_set(
    spec: NKSpec,
    record: Mapping[str, Any],
    _registry: NKRegistry,
) -> NaturalKey:
    """Cable-style NK: unordered set of `(content_type, NK)` pairs.

    NetBox 4.x cables expose `a_terminations` and `b_terminations`
    as lists of `{object_type, object_id}` dicts. We surface them
    as a *sorted* tuple so the NK is stable across runs.
    """

    if spec.content_type != "dcim.cable":  # pragma: no cover, defensive
        msg = f"polymorphic_set strategy not supported for {spec.content_type}"
        raise ValueError(msg)
    a = tuple(_termination_tuple(t) for t in record.get("a_terminations") or [])
    b = tuple(_termination_tuple(t) for t in record.get("b_terminations") or [])
    # The cable's two ends are interchangeable, so we sort each side
    # then sort the pair so swapping a and b produces the same NK.
    sides = tuple(sorted([tuple(sorted(a)), tuple(sorted(b))]))
    return sides


def _termination_tuple(termination: Mapping[str, Any]) -> tuple[str, Any]:
    """Read one cable termination shape into a (type, id-or-nk) tuple.

    NetBox's raw response shape is `{object_type, object_id, object}`
    where the id is an integer. After the export-side FK rewriter
    runs, the id is replaced by `object_natural_key` carrying the
    target's NK tuple. We accept both shapes so NK computation
    works whether the resolver is called before the rewrite
    (raw record) or after (rewritten body).
    """
    object_type = str(termination.get("object_type") or "")
    if "object_natural_key" in termination:
        return (object_type, termination["object_natural_key"])
    return (object_type, termination.get("object_id"))
