"""Natural-key dataclasses (FEAT-08a).

The natural-key registry tells the exporter how to identify each
record without using the source DB's primary key. The import side
uses the same metadata in reverse: given a (content_type, NK
tuple) it can find the destination's local id.

Three strategies cover the vast majority of NetBox models:

* `slug`: a single field, usually `slug`, sometimes `name`. The
  field value is unique inside the model.
* `composite`: a tuple of fields, sometimes with a parent FK.
  Example: VLAN is `(site, vid)` or `(group, vid)`.
* `polymorphic_set`: used for the two ends of a Cable. The NK is
  the unordered set of `(content_type, NK)` pairs.

The dataclasses below are immutable so the registry can be safely
shared across the whole export run.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum


class Strategy(Enum):
    """Which resolution strategy to apply for a content type."""

    SLUG = "slug"
    COMPOSITE = "composite"
    POLYMORPHIC_SET = "polymorphic_set"


@dataclass(frozen=True)
class NKField:
    """One field that participates in a natural key.

    `parent_content_type` is set when the field is itself a FK,
    in which case the resolver must descend into the parent's NK
    rather than the FK id.
    """

    name: str
    parent_content_type: str | None = None


@dataclass(frozen=True)
class NKSpec:
    """How to build a natural key for one content type."""

    content_type: str
    strategy: Strategy
    fields: tuple[NKField, ...]

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------
    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)


class NKRegistry:
    """In-memory map content_type -> NKSpec."""

    def __init__(self) -> None:
        self._by_ct: dict[str, NKSpec] = {}

    def register(self, spec: NKSpec) -> None:
        """Add or replace the NKSpec for a content type."""
        self._by_ct[spec.content_type] = spec

    def get(self, content_type: str) -> NKSpec:
        try:
            return self._by_ct[content_type]
        except KeyError:
            msg = f"no NKSpec registered for {content_type!r}"
            raise KeyError(msg) from None

    def has(self, content_type: str) -> bool:
        return content_type in self._by_ct

    def __iter__(self) -> Iterator[NKSpec]:
        return iter(self._by_ct.values())

    def __len__(self) -> int:
        return len(self._by_ct)
