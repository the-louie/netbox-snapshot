"""Schema drift detection between snapshot and destination OpenAPI.

The snapshot carries the schema of the NetBox it was exported
from. The destination has its own schema. When the two differ
on a field's foreign-key target or its is_m2m shape, the
import-side resolver can place the wrong destination id (or
none at all) because it consults the snapshot's schema for
shape decisions.

`diff_schemas` walks every (content_type, field) pair in the
operator-supplied scope and reports the FieldDrift entries
where the two schemas disagree at the FK level.

Ground truth: the helper relies on `OpenAPI.field_spec`, which
collapses a NetBox schema response into the
`(fk_target, is_m2m, nullable, ...)` tuple the resolver
operates on. Diffing at the FieldSpec level rather than at the
raw JSON gives a stable signal even when NetBox cosmetically
reshapes its schema between versions.
"""

from __future__ import annotations

from dataclasses import dataclass

from nbsnap.schema.openapi import OpenAPI


@dataclass(frozen=True)
class FieldDrift:
    """One per (content_type, field) where the two schemas disagree.

    `snapshot_shape` and `destination_shape` are short human
    summaries of the field's resolver-visible shape. They are
    NOT structured because the consumer is the operator
    reading the CLI summary; a tool consuming the audit JSONL
    can reconstruct from `content_type` + `field` + the two
    raw schemas.
    """

    content_type: str
    field: str
    snapshot_shape: str
    destination_shape: str


def _shape_summary(spec: object) -> str:
    """Render a FieldSpec as a short human string for the diff."""

    fk_target = getattr(spec, "fk_target", None)
    is_m2m = getattr(spec, "is_m2m", False)
    if fk_target is None:
        return "scalar" if not is_m2m else "scalar-m2m"
    return f"{fk_target}{' (m2m)' if is_m2m else ''}"


def diff_schemas(
    snapshot: OpenAPI,
    destination: OpenAPI,
    scope: set[str],
) -> list[FieldDrift]:
    """Return per-field drift entries for content types in `scope`.

    Only FK shape and is_m2m differences trip the diff today.
    `nullable` / `required` differences are intentionally
    ignored because NetBox tightens those between point
    releases without breaking the import-side resolver.

    Iteration is keyed on the union of field names visible to
    each schema, so a field that exists on one side but not
    the other registers as drift (`destination_shape =
    "<missing>"` etc.).
    """

    drift: list[FieldDrift] = []
    for ct in sorted(scope):
        snap_fields = _writable_fields(snapshot, ct)
        dest_fields = _writable_fields(destination, ct)
        all_fields = sorted(snap_fields | dest_fields)
        for field_name in all_fields:
            snap_summary = _spec_summary_or_missing(snapshot, ct, field_name, snap_fields)
            dest_summary = _spec_summary_or_missing(destination, ct, field_name, dest_fields)
            if snap_summary == dest_summary:
                continue
            drift.append(
                FieldDrift(
                    content_type=ct,
                    field=field_name,
                    snapshot_shape=snap_summary,
                    destination_shape=dest_summary,
                )
            )
    return drift


def _writable_fields(schema: OpenAPI, content_type: str) -> set[str]:
    """Best-effort field-name enumeration for `content_type`.

    Uses `write_allowlist` because it is the authoritative
    source for write-relevant fields. A content type that the
    destination does not expose at all returns an empty set,
    which the diff renders as `<missing>` on that side.
    """
    try:
        return set(schema.write_allowlist(content_type))
    except Exception:  # noqa: BLE001 - best effort
        return set()


def _spec_summary_or_missing(
    schema: OpenAPI,
    ct: str,
    field_name: str,
    present: set[str],
) -> str:
    if field_name not in present:
        return "<missing>"
    try:
        return _shape_summary(schema.field_spec(ct, field_name))
    except Exception:  # noqa: BLE001
        return "<unreadable>"
