"""Natural-key index for the destination NetBox (FEAT-19a/b).

The importer builds an index `(content_type, NK) -> destination_id`
on demand. For each content type the index is populated by walking
the destination's list endpoint with `brief=true` to keep the
payload small.

The index supports lookups (FK resolution) and insertions (after
a successful POST/PATCH on a new record).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from nbsnap.http.client import NetboxHTTP
from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.resolver import NaturalKey, resolve

# Map from content type to the API path the destination uses for
# its brief listing. Re-uses the table maintained in verify so
# changes happen in one place.
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS


@dataclass
class NKIndex:
    """`(content_type, NK) -> destination_id` map.

    Populated lazily: when a caller asks for a content type that
    has not been built yet, the index issues a brief listing and
    caches every row.
    """

    _by_key: dict[tuple[str, NaturalKey], int] = field(default_factory=dict)
    _built_cts: set[str] = field(default_factory=set)

    def ensure_built(
        self,
        http: NetboxHTTP,
        registry: NKRegistry,
        content_type: str,
        *,
        _building: set[str] | None = None,
    ) -> None:
        """Populate the index for `content_type`, recursively
        building every content type its NKSpec references.

        Composite NKs reference other content types through
        `NKField.parent_content_type`. A lookup against a deep NK
        (e.g. `ipam.ipaddress` whose `assigned_object_id` is an
        `dcim.interface` NK that itself depends on `dcim.device`)
        only succeeds when every parent index is present.

        `_building` is the active recursion stack; a content
        type already on the stack is skipped, which is how we
        tolerate self-referencing NKSpecs like
        `dcim.devicerole.parent -> dcim.devicerole`.
        """

        if content_type in self._built_cts:
            return
        if _building is None:
            _building = set()
        if content_type in _building:
            # Cycle, skip. The partial NK we can compute without
            # the deeper level is the best we can do; the
            # alternative is to recurse forever.
            return
        _building.add(content_type)

        # Walk the NKSpec's parent dependencies first so the
        # nested resolve() calls below find every level they need.
        if registry.has(content_type):
            spec = registry.get(content_type)
            for field_spec in spec.fields:
                if field_spec.parent_content_type is not None:
                    self.ensure_built(
                        http,
                        registry,
                        field_spec.parent_content_type,
                        _building=_building,
                    )

        endpoint = CONTENT_TYPE_ENDPOINTS.get(content_type)
        if endpoint is None:
            self._built_cts.add(content_type)
            _building.discard(content_type)
            return

        # `brief=true` keeps the response small. NetBox strips most
        # nested representations from briefs, so the lookup table
        # uses bare ids and slug/name.
        sep = "&" if "?" in endpoint else "?"
        for row in http.get_all(f"{endpoint}{sep}brief=true"):
            try:
                nk = resolve(registry, content_type, row)
            except (KeyError, ValueError):
                continue
            rid = row.get("id")
            if isinstance(rid, int):
                self._by_key[(content_type, nk)] = rid

        self._built_cts.add(content_type)
        _building.discard(content_type)

    def lookup(self, content_type: str, nk: NaturalKey) -> int | None:
        return self._by_key.get((content_type, nk))

    def insert(self, content_type: str, nk: NaturalKey, destination_id: int) -> None:
        self._by_key[(content_type, nk)] = destination_id

    def __len__(self) -> int:
        return len(self._by_key)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, tuple) and key in self._by_key

    def all_for_content_type(self, content_type: str) -> Mapping[NaturalKey, int]:
        return {nk: i for (ct, nk), i in self._by_key.items() if ct == content_type}


def _silence_unused() -> None:  # pragma: no cover
    _ = Any
