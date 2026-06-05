"""ARCH-01b: :class:`nbsnap.snapshot.manifest.Manifest` parity tests.

Mirrors the existing ``tests/unit/test_export_manifest_and_progress.py``
coverage but uses the new canonical import path under
:mod:`nbsnap.snapshot.manifest`. Both files coexist during the
ARCH-01e/f migration window so a regression in either path surfaces
loudly; ARCH-01f will retire the legacy file once every consumer
imports from ``nbsnap.snapshot``.
"""

from __future__ import annotations

from pathlib import Path

from nbsnap.snapshot.manifest import (
    MANIFEST_FILENAME,
    SOURCE_URL_HASH_LENGTH,
    Manifest,
    compute_source_url_hash,
)


def test_manifest_roundtrip(tmp_path: Path) -> None:
    m = Manifest(
        source_url_hash="deadbeef0001", netbox_version="4.6.2", counts={"a": 1}
    )
    m.write(tmp_path / MANIFEST_FILENAME)
    loaded = Manifest.load(tmp_path / MANIFEST_FILENAME)
    assert loaded.source_url_hash == "deadbeef0001"
    assert loaded.counts == {"a": 1}


def test_compute_source_url_hash_length() -> None:
    assert len(compute_source_url_hash("https://x/")) == SOURCE_URL_HASH_LENGTH


def test_legacy_export_manifest_re_exports_same_objects() -> None:
    """The ``export.manifest`` shim must point at the same objects.

    If any of the re-exports ever drifted to copies we would get two
    distinct definitions and ``isinstance`` checks (or ``is``
    comparisons) across the migration window would fail subtly. Pin
    object identity on every member of the contract here.
    """

    from nbsnap.export import manifest as legacy

    assert legacy.Manifest is Manifest
    assert legacy.MANIFEST_FILENAME is MANIFEST_FILENAME
    assert legacy.compute_source_url_hash is compute_source_url_hash
    assert legacy.SOURCE_URL_HASH_LENGTH is SOURCE_URL_HASH_LENGTH
