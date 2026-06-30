"""ARCH-01b: :class:`nbsnap.snapshot.manifest.Manifest` parity tests.

Mirrors the existing ``tests/unit/test_export_manifest_and_progress.py``
coverage but uses the new canonical import path under
:mod:`nbsnap.snapshot.manifest`. Both files coexist during the
ARCH-01e/f migration window so a regression in either path surfaces
loudly; ARCH-01f will retire the legacy file once every consumer
imports from ``nbsnap.snapshot``.
"""

from __future__ import annotations

import json
from pathlib import Path

from nbsnap.snapshot.manifest import (
    MANIFEST_FILENAME,
    SOURCE_URL_HASH_LENGTH,
    Manifest,
    compute_source_url_hash,
)


def test_manifest_roundtrip(tmp_path: Path) -> None:
    m = Manifest(source_url_hash="deadbeef0001", netbox_version="4.6.2", counts={"a": 1})
    m.write(tmp_path / MANIFEST_FILENAME)
    loaded = Manifest.load(tmp_path / MANIFEST_FILENAME)
    assert loaded.source_url_hash == "deadbeef0001"
    assert loaded.counts == {"a": 1}


def test_compute_source_url_hash_length() -> None:
    assert len(compute_source_url_hash("https://x/")) == SOURCE_URL_HASH_LENGTH


def test_load_drops_legacy_pre_sec04a_source_url(tmp_path: Path) -> None:
    """BUG-16: pre-SEC-04a snapshots carry a literal ``source_url``.

    The frozen rescue snapshot under
    ``/workspace/snapshot-source-frozen/`` was exported before
    SEC-04a replaced the literal field with ``source_url_hash``.
    ``Manifest.load`` must drop the legacy key on the way in, the
    leaked URL must not survive into the in-memory manifest, and
    no other declared field may change shape.
    """

    legacy_payload = {
        "version": 1,
        "source_url": "https://localhost:8443",
        "netbox_version": "4.6.2",
        "nbsnap_version": "0.0.1",
        "created_at": "2026-06-15T08:48:10.594220+00:00",
        "counts": {"dcim.site": 10},
        "perf": {},
        "deferred_edges": [],
    }
    path = tmp_path / MANIFEST_FILENAME
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = Manifest.load(path)

    assert loaded.source_url_hash == ""
    assert loaded.netbox_version == "4.6.2"
    assert loaded.counts == {"dcim.site": 10}
    assert not hasattr(loaded, "source_url")


def test_legacy_export_manifest_no_longer_re_exports() -> None:
    """ARCH-01f removed the back-compat re-exports.

    After ARCH-01f the legacy module ``nbsnap.export.manifest``
    exposes only :class:`PerfTimer` (export-side instrumentation).
    The contract symbols (``Manifest``, ``MANIFEST_FILENAME``, and
    the SEC-04a hash helpers) live exclusively at
    :mod:`nbsnap.snapshot.manifest`. This test pins the deletion so
    a future contributor cannot accidentally re-introduce the shim.
    """

    from nbsnap.export import manifest as legacy

    assert not hasattr(legacy, "Manifest")
    assert not hasattr(legacy, "MANIFEST_FILENAME")
    assert not hasattr(legacy, "compute_source_url_hash")
    # PerfTimer stays where it was, it is export-only instrumentation.
    assert hasattr(legacy, "PerfTimer")
