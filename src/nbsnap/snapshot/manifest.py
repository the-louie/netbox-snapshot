"""Snapshot manifest dataclass and filename constant (ARCH-01b).

This is the canonical home of the manifest contract that sits at
the top of every snapshot directory.

History
-------
Before ARCH-01b the dataclass lived under :mod:`nbsnap.export.manifest`
even though :mod:`nbsnap.import_` was a major consumer. Three import
sites under ``import_/`` reached into ``export/`` for the contract,
which made the two packages asymmetrically coupled.

ARCH-01b moves the symbol to :mod:`nbsnap.snapshot.manifest` (this
file). The old location continues to re-export both names during
the ARCH-01e/f migration window so existing callers keep compiling;
the re-exports go away with ARCH-01f.

Security note
-------------
SEC-04a replaced the literal ``source_url`` field with
``source_url_hash``. The hash, not the URL, is what reaches disk.
See :func:`compute_source_url_hash` for the derivation rationale.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "manifest.json"

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
        Path(path).write_text(json.dumps(asdict(self), sort_keys=True, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Manifest:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        # Drop unknown keys so pre-SEC-04a snapshots (which still
        # carry the literal `source_url`) load without raising.
        # Dropping is the right posture, not migrating: SEC-04a
        # treats the URL as install-local data that must not reach
        # the in-memory manifest, and we will not reconstruct the
        # provenance hash from a leaked URL on load.
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


__all__ = [
    "MANIFEST_FILENAME",
    "Manifest",
    "SOURCE_URL_HASH_LENGTH",
    "compute_source_url_hash",
]
