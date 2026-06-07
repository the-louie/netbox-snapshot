"""Content-type cache and diff helpers (FEAT-03a/b).

NetBox uses an integer `id` per content type that varies between
installations. The export side serialises `(app, model)` tuples and
the import side translates them back to the destination's local
ids via this cache.

Per Q13 burndown, the cache probes both `extras/content-types/`
(NetBox 4.6+ legacy alias) and `extras/object-types/` (current
NetBox 4.6+ preferred path). Whichever responds is the working
endpoint, and we remember it on the cache instance.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nbsnap.http.client import NetboxHTTP

# NetBox renamed and relocated this concept twice in the 3.x -> 4.x
# transition:
#   * 3.x and 4.0:  extras/content-types/
#   * 4.0:          extras/object-types/   (rename, same app)
#   * 4.1+:         core/object-types/     (relocation to the core app)
#
# We probe newest first so a modern install short-circuits the
# scan. Each path is tried exactly once, the first 200 wins and is
# cached on the instance as `endpoint_used`. Confirmed against a
# production NetBox 4.6.2 install where only `core/object-types/`
# responds.
_CONTENT_TYPE_ENDPOINTS: tuple[str, ...] = (
    "core/object-types/",
    "extras/object-types/",
    "extras/content-types/",
)


@dataclass(frozen=True)
class ContentTypeDelta:
    """Shape of the diff between two content-type caches."""

    only_on_source: set[tuple[str, str]]
    only_on_destination: set[tuple[str, str]]
    common: set[tuple[str, str]]


class ContentTypeCache:
    """Bidirectional `(app, model) <-> id` map.

    Read-only after `fetch`. Lookups raise `KeyError` on miss so
    silent fall-through is impossible.
    """

    def __init__(
        self,
        forward: dict[tuple[str, str], int],
        endpoint_used: str,
    ) -> None:
        self._forward = dict(forward)
        # Reverse index built once at construction so `natural_for`
        # is constant-time.
        self._reverse = {v: k for k, v in self._forward.items()}
        self.endpoint_used = endpoint_used

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def fetch(cls, http: NetboxHTTP) -> ContentTypeCache:
        """Probe the two known endpoints; return the populated cache.

        Stops at the first endpoint that does not 404. If both
        endpoints 404, raise so the operator sees the problem
        instead of getting a silent empty cache.
        """

        # ARCH-07d: NetboxHTTPError is a nbsnap-domain exception
        # exported from nbsnap.http; the catch is appropriate here
        # because we deliberately probe legacy 404-only endpoints.
        from nbsnap.http import NetboxHTTPError

        last_error: Exception | None = None
        for endpoint in _CONTENT_TYPE_ENDPOINTS:
            try:
                rows = list(http.get_all(endpoint))
            except NetboxHTTPError as exc:
                if exc.status == 404:
                    last_error = exc
                    continue
                raise
            forward = cls._rows_to_forward(rows)
            return cls(forward, endpoint_used=endpoint)

        msg = (
            "neither extras/content-types/ nor extras/object-types/ responded; "
            "is this a NetBox install? last error: "
            f"{last_error!r}"
        )
        raise RuntimeError(msg)

    @staticmethod
    def _rows_to_forward(rows: list[dict[str, object]]) -> dict[tuple[str, str], int]:
        forward: dict[tuple[str, str], int] = {}
        for row in rows:
            app = row.get("app_label")
            model = row.get("model")
            ct_id = row.get("id")
            if not (isinstance(app, str) and isinstance(model, str) and isinstance(ct_id, int)):
                continue
            forward[(app, model)] = ct_id
        return forward

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def id_for(self, app: str, model: str) -> int:
        """Return the integer id for `(app, model)`, raising on miss."""
        try:
            return self._forward[(app, model)]
        except KeyError:
            msg = f"content type {app}.{model} not present in this NetBox"
            raise KeyError(msg) from None

    def natural_for(self, ct_id: int) -> tuple[str, str]:
        """Return the `(app, model)` tuple for a given id, raising on miss."""
        try:
            return self._reverse[ct_id]
        except KeyError:
            msg = f"content type id {ct_id} not present in this NetBox"
            raise KeyError(msg) from None

    def has(self, app: str, model: str) -> bool:
        """Return True iff `(app, model)` is present, no exception."""
        return (app, model) in self._forward

    # ------------------------------------------------------------------
    # Iteration and diagnostics
    # ------------------------------------------------------------------
    def __iter__(self) -> Iterator[tuple[str, str, int]]:
        for (app, model), ct_id in self._forward.items():
            yield app, model, ct_id

    def __len__(self) -> int:
        return len(self._forward)

    def __contains__(self, key: object) -> bool:
        return key in self._forward

    # ------------------------------------------------------------------
    # Diff (FEAT-03b)
    # ------------------------------------------------------------------
    def diff(self, other: ContentTypeCache) -> ContentTypeDelta:
        """Compute the set differences against another cache."""
        a = set(self._forward.keys())
        b = set(other._forward.keys())
        return ContentTypeDelta(
            only_on_source=a - b,
            only_on_destination=b - a,
            common=a & b,
        )


def format_delta_for_operator(delta: ContentTypeDelta) -> str:
    """Render a `ContentTypeDelta` as a tidy ASCII table for run summaries."""

    def _block(title: str, rows: set[tuple[str, str]]) -> str:
        if not rows:
            return f"{title}: (none)\n"
        lines = "\n".join(f"  {app}.{model}" for app, model in sorted(rows))
        return f"{title}:\n{lines}\n"

    return (
        _block("only on source", delta.only_on_source)
        + _block("only on destination", delta.only_on_destination)
        + f"common: {len(delta.common)}\n"
    )
