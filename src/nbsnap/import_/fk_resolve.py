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


class ResolverFKMissError(KeyError):
    """Raised when an FK natural-key has no match on the destination.

    ARCH-09b. The legacy raise was a bare :class:`KeyError`; this
    typed exception carries record-level context so the audit row
    points the operator at the offending ``(child_content_type,
    natural_key)`` and names the FK ``target_ct`` they should be
    chasing on the destination.

    Attributes:

    * ``content_type``  : the child record whose FK could not resolve
      (e.g. ``"dcim.device"`` carrying a ``primary_ip4`` FK).
    * ``natural_key``   : the child record's NK; useful so the
      operator can find the offending row in the snapshot.
    * ``target_ct``     : the parent content type the FK pointed at
      (e.g. ``"ipam.ipaddress"``).
    * ``hint``          : one of the three likely causes (missing
      source data, scope mismatch, schema skew). Free-form string
      so a new hint can land at a raise site without a wider patch.

    Inherits :class:`KeyError` so the legacy ``except KeyError``
    clauses keep working during the migration window.
    """

    def __init__(
        self,
        message: str,
        *,
        content_type: str,
        natural_key: tuple[Any, ...] | None,
        target_ct: str,
        hint: str,
    ) -> None:
        self.content_type = content_type
        self.natural_key = natural_key
        self.target_ct = target_ct
        self.hint = hint
        self._message = message
        super().__init__(self._render())

    def _render(self) -> str:
        return (
            f"[{self.content_type} {self.natural_key} -> {self.target_ct}] "
            f"{self._message} (hint: {self.hint})"
        )

    def __str__(self) -> str:
        # ``KeyError`` overrides ``__str__`` to ``repr(message)`` which
        # wraps the string in extra quotes; the audit consumer wants
        # the bare message, not the quoted form. Override here so the
        # bracketed prefix lands at column zero and ``str.startswith``
        # works for grep-style consumers.
        return self._render()


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
        # ARCH-09c: anchor what the leaf knows. We do not know the
        # child's content_type or its NK here, those are caller
        # context, the caller can wrap and re-raise with the child
        # detail if it wants a more specific audit row. Leaf-level
        # we set content_type = target_ct so the rendered prefix
        # still reads correctly. ``hint`` is "missing source data"
        # because in practice the parent row simply was not in the
        # source export, scope-mismatch and schema-skew are rarer
        # in this code path.
        raise ResolverFKMissError(
            f"NK {nk!r} not found on destination",
            content_type=parent_ct,
            natural_key=nk if isinstance(nk, tuple) else None,
            target_ct=parent_ct,
            hint="missing source data",
        )
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
    resolved = resolve_simple_fk(object_nk, object_type, index, http=http, registry=registry)
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
