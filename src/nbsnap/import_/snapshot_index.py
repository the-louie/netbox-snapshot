"""In-memory (content_type, NK) -> body map for look-ahead resolution.

The destination NetBox's `NKIndex` answers "does this record exist
on the destination right now?". When that misses, the demand-driven
resolver (FEAT-36b, lands later) wants a second tier that answers
"is this record in the SNAPSHOT we are importing?". If yes, the
resolver creates it on the destination on demand, recursively if
needed, rather than dropping the FK.

This module is the second tier. We build it once at the top of
`run_import` and lookups are pure dict access from then on.

Memory footprint. Each row's body dict is stored as-is. A typical
renderer-minimum snapshot is around 5,000 rows with bodies in the
~100-byte range, so the index occupies roughly 5 MB of RAM. Even
a 500,000-row snapshot fits in well under 100 MB, comfortably
within any operator-host budget. A streaming variant becomes
interesting only beyond that scale.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["NaturalKey", "SnapshotIndex", "iter_jsonl"]

# A natural key in the snapshot's JSONL is a JSON-deserialised
# list-of-lists. We normalise to tuple-of-tuples at load time so
# the index key is hashable and equality works cleanly across
# composite NKs of any depth.
NaturalKey = tuple[Any, ...]


def _to_tuple(value: Any) -> Any:
    """Recursively convert lists to tuples so the value is hashable.

    Useful for converting NK shapes that JSON deserialises as
    lists into tuples for use as dict keys. Leaves non-list
    values unchanged.
    """

    if isinstance(value, list):
        return tuple(_to_tuple(v) for v in value)
    return value


@dataclass
class SnapshotIndex:
    """Maps `(content_type, NK) -> snapshot body`.

    Read-only after construction by convention; callers should
    treat the returned body dicts as immutable. Mutating a
    returned body would corrupt the index for future lookups.
    """

    _by_key: dict[tuple[str, NaturalKey], dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_snapshot(cls, snapshot_dir: Path) -> SnapshotIndex:
        """Walk every JSONL under `snapshot_dir`, build the index.

        Skips known audit-log files (`flags.jsonl`,
        `progress.jsonl`, `_deferred.jsonl`, `audit.jsonl`)
        because they are not record streams. Unknown JSONL paths
        (e.g. a future content type that does not appear in
        `CONTENT_TYPE_FILES`) are also skipped silently; the
        importer will surface the gap as an out-of-scope drop
        when it gets there.

        Malformed rows are silently skipped. The export pipeline
        already routes those through `flags.jsonl`, so reaching
        a malformed row here would indicate hand-edited JSONL
        and the defensive skip keeps the loader from aborting on
        a single bad line.
        """

        # CONTENT_TYPE_FILES maps content_type -> "<app>/<file>.jsonl".
        # We invert that mapping so we can recognise each jsonl
        # path and tag every row with its content type.
        from nbsnap.export.writer import CONTENT_TYPE_FILES

        ct_by_rel = {rel: ct for ct, rel in CONTENT_TYPE_FILES.items()}

        index = cls()
        for jsonl_path in snapshot_dir.rglob("*.jsonl"):
            # Audit-log files are not record streams; ignore.
            if jsonl_path.name in {
                "flags.jsonl",
                "progress.jsonl",
                "_deferred.jsonl",
                "audit.jsonl",
            }:
                continue
            rel = jsonl_path.relative_to(snapshot_dir).as_posix()
            content_type = ct_by_rel.get(rel)
            if content_type is None:
                # Unknown jsonl; skip. The driver will log a
                # warning if it actually needs records from this
                # file.
                continue
            for row in iter_jsonl(jsonl_path):
                nk = _to_tuple(row.get("natural_key"))
                body = row.get("body") or {}
                if isinstance(body, dict):
                    index._by_key[(content_type, nk)] = body
        return index

    def lookup(
        self, content_type: str, natural_key: NaturalKey
    ) -> dict[str, Any] | None:
        """Return the snapshot body for `(content_type, NK)` or None.

        The NK can be a tuple OR a list (callers passing the
        result of `json.loads(...)` get a list). We normalise to
        tuple on the way in so list-shaped queries still hit.
        """

        return self._by_key.get((content_type, _to_tuple(natural_key)))

    def has(self, content_type: str, natural_key: NaturalKey) -> bool:
        """Constant-time membership check.

        Equivalent to `lookup(...) is not None` but skips
        constructing the `None` indirection, which matters when
        the demand-driven resolver tests this on every FK in
        every row of an import.
        """

        return (content_type, _to_tuple(natural_key)) in self._by_key

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[tuple[str, NaturalKey]]:
        """Iterate every `(content_type, NK)` key the index carries.

        Mostly useful for diagnostics like "how many rows did we
        load?", not for normal lookup paths.
        """

        return iter(self._by_key.keys())


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a JSONL file, skipping blank or malformed lines silently.

    The export pipeline already routes broken rows through
    `flags.jsonl`, so reaching a malformed row here would
    indicate hand-edited JSONL. We defend against it by
    swallowing JSONDecodeError per-line; the operator can
    inspect the file independently if they suspect corruption.
    """

    with path.open(encoding="utf-8") as fp:
        for raw in fp:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue
