"""FK resolvers (FEAT-20a/b/c).

Mirror of `export/fk_rewrite.py`. Where the export side turns ids
into NK tuples, the import side turns NK tuples back into ids using
the destination's NKIndex.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.nk_index import NKIndex
from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.resolver import NaturalKey


def resolve_simple_fk(
    nk: Any,
    parent_ct: str,
    index: NKIndex,
    *,
    http: NetboxHTTP,
    registry: NKRegistry,
) -> int | None:
    """Resolve a single NK tuple to the destination id."""

    if nk is None:
        return None
    index.ensure_built(http, registry, parent_ct)
    if isinstance(nk, list):
        nk = tuple(_to_tuple(v) for v in nk)
    if not isinstance(nk, tuple):
        return None
    resolved = index.lookup(parent_ct, nk)
    if resolved is None:
        msg = f"NK {nk!r} for {parent_ct} not found on destination"
        raise KeyError(msg)
    return resolved


def resolve_m2m(
    values: Any, parent_ct: str, index: NKIndex, *, http: NetboxHTTP, registry: NKRegistry
) -> list[int]:
    """Resolve every NK in an m2m list."""
    if not isinstance(values, list):
        return []
    out: list[int] = []
    for v in values:
        resolved = resolve_simple_fk(v, parent_ct, index, http=http, registry=registry)
        if resolved is not None:
            out.append(resolved)
    return out


def resolve_polymorphic(
    value: Mapping[str, Any], index: NKIndex, *, http: NetboxHTTP, registry: NKRegistry
) -> dict[str, Any] | None:
    """Resolve a `{object_type, object_natural_key}` pair to ids."""

    object_type = value.get("object_type")
    object_nk = value.get("object_natural_key")
    if not isinstance(object_type, str):
        return None
    resolved = resolve_simple_fk(
        object_nk, object_type, index, http=http, registry=registry
    )
    if resolved is None:
        return None
    return {"object_type": object_type, "object_id": resolved}


def _to_tuple(value: Any) -> Any:
    """Convert JSON-deserialised lists back into tuples for NK lookup."""

    if isinstance(value, list):
        return tuple(_to_tuple(v) for v in value)
    return value


def normalise_nk(value: Any) -> NaturalKey:
    """Turn a JSON-deserialised NK (list-of-lists) into a tuple-of-tuples."""

    converted = _to_tuple(value)
    if isinstance(converted, tuple):
        return converted
    return (converted,)
