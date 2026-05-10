"""FK rewriter, simple, m2m, polymorphic (FEAT-12a/b/c).

Every FK in the snapshot is rewritten from the source's numeric id
to a natural-key tuple the destination can resolve. The rewriter
takes a single record, the natural-key registry, and a
parent-lookup table populated by the export driver. It returns a
new record with the FK fields replaced.

Three flavours:

* Simple FK, the field is one id or a nested dict, the rewriter
  resolves to one NK tuple.
* M2M, the field is a list, the rewriter resolves each item.
* Polymorphic, the field carries both an `object_type` and an
  `object_id`. The rewriter resolves the id against the indicated
  content type.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.resolver import resolve

# A parent_lookup maps (content_type, source_id) -> record dict.
# The export driver builds it incrementally as it walks endpoints.
ParentLookup = Mapping[tuple[str, int], Mapping[str, Any]]


def rewrite_simple_fk(
    value: Any, parent_ct: str, registry: NKRegistry, parent_lookup: ParentLookup
) -> Any:
    """Rewrite a single-FK field value to a natural-key tuple.

    Args:
        value: The FK as returned by NetBox; can be a nested dict,
            a bare int, or `None`.
        parent_ct: The content type the FK points at.
        registry: Natural-key registry, source of NK strategy.
        parent_lookup: `(content_type, id) -> record` map.

    Returns:
        The natural-key tuple, or `None` when the source FK was
        null.
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        return resolve(registry, parent_ct, value, parent_lookup=parent_lookup)
    if isinstance(value, int):
        parent_record = parent_lookup.get((parent_ct, value))
        if parent_record is None:
            msg = (
                f"FK to {parent_ct} id {value} cannot be resolved; "
                "parent_lookup is missing this record"
            )
            raise ValueError(msg)
        return resolve(registry, parent_ct, parent_record, parent_lookup=parent_lookup)
    return value


def rewrite_m2m(
    values: Any, parent_ct: str, registry: NKRegistry, parent_lookup: ParentLookup
) -> list[Any]:
    """Rewrite a many-to-many FK list.

    Returns a list (not tuple) so JSON serialisation surfaces the
    m2m shape; the importer will turn it back into the right verb.
    """
    if not isinstance(values, list):
        return []
    return [rewrite_simple_fk(v, parent_ct, registry, parent_lookup) for v in values]


def rewrite_polymorphic(
    value: Any, registry: NKRegistry, parent_lookup: ParentLookup
) -> dict[str, Any] | None:
    """Rewrite a polymorphic FK `{object_type, object_id}` field.

    Returns a `{object_type, object_natural_key}` dict so the
    importer can do the inverse lookup against the destination's
    NK index.

    When the input carries a nested `object` field (NetBox 4.x
    cable terminations and similar shapes include the full record
    inline next to the id and type), we prefer it over the bare id
    path so the resolver does not depend on parent_lookup being
    populated for the target content type.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    object_type = value.get("object_type")
    object_id = value.get("object_id")
    if not isinstance(object_type, str) or not isinstance(object_id, int):
        return None
    nested = value.get("object")
    if isinstance(nested, Mapping):
        nk = rewrite_simple_fk(nested, object_type, registry, parent_lookup)
    else:
        nk = rewrite_simple_fk(object_id, object_type, registry, parent_lookup)
    return {"object_type": object_type, "object_natural_key": nk}
