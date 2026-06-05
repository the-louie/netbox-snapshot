"""Snapshot manifest dataclass (FEAT-15a) and PerfTimer (FEAT-15b).

The manifest is the single file at the top of the snapshot that
tells the importer (and the operator) what the snapshot contains.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "manifest.json"

# Length of the truncated sha256 we persist in the manifest as
# ``source_url_hash``. Twelve hex chars = 48 bits of entropy, more than
# enough to disambiguate the handful of NetBox instances any operator
# tracks while staying short enough to read on a single transcript
# line. Centralised here so the driver and the test agree.
SOURCE_URL_HASH_LENGTH = 12


def compute_source_url_hash(source_url: str) -> str:
    """Derive the short, deterministic provenance hash for a source URL.

    SEC-04a replaced the literal ``source_url`` field on
    :class:`Manifest` with this hash. The hash is provenance only,
    "this snapshot came from the same NetBox as that other snapshot",
    not a reachable URL. Two snapshots from the same source produce
    identical hashes; two snapshots from different sources do not.

    UTF-8 encoding is hard-coded because URLs are required to be
    ASCII at the wire level; any non-ASCII would be percent-encoded
    before reaching us. We do not normalise (trailing slash, case)
    because the same operator invokes the same CLI flag each time
    and the URL string is already stable.
    """

    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return digest[:SOURCE_URL_HASH_LENGTH]


@dataclass
class Manifest:
    """Top-level snapshot description.

    The ``source_url_hash`` field carries a short, deterministic
    fingerprint of the source NetBox's base URL (see
    :func:`compute_source_url_hash`). Before SEC-04a we persisted the
    literal URL; that contradicted the "no install-local data in the
    snapshot" rule (see ``goals.md`` and the "scope" banner in
    ``CLAUDE.md``) and gave anyone with read access to a leaked
    snapshot the source's network coordinates. The hash gives us
    provenance ("did these two snapshots come from the same source?")
    without leaking the address.
    """

    version: int = 1
    source_url_hash: str = ""
    netbox_version: str = "unknown"
    nbsnap_version: str = "0.0.1"
    created_at: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    perf: dict[str, float] = field(default_factory=dict)
    deferred_edges: list[dict[str, Any]] = field(default_factory=list)

    def write(self, path: Path) -> None:
        """Write the manifest as canonical JSON."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(asdict(self), sort_keys=True, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> Manifest:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)


class PerfTimer:
    """Lightweight wall-clock timer for the manifest's perf section.

    Use as a context manager. The accumulated seconds land in the
    dict the timer was given so the caller does not have to manage
    the bookkeeping.
    """

    def __init__(self, sink: dict[str, float]) -> None:
        self._sink = sink

    @contextmanager
    def timer(self, label: str):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        try:
            yield
        finally:
            self._sink[label] = self._sink.get(label, 0.0) + time.perf_counter() - start
