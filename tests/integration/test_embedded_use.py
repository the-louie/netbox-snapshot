"""ARCH-11d: a script can import nbsnap and drive it via the public API.

A user embedding nbsnap inside another tool reaches for the top-level
symbols, ``nbsnap.run_export``, ``nbsnap.run_import``, ``nbsnap.Manifest``,
and ``nbsnap.__version__``. This test pretends to be that user.

What we exercise:

* The four top-level symbols import cleanly.
* :func:`run_export` and :func:`run_import` are callables with the
  expected signature (HTTP client first, output/input path second).
* A Manifest can be constructed, serialised to disk, and re-loaded
  from disk through the public surface only.

We deliberately do not run a full export-then-import cycle here, the
in-process cycle needs a mocked OpenAPI fetch and a NetBox response
suite that the existing :mod:`tests.integration.test_import_demand_driven`
already covers. This test pins the *embedding contract*, not the
end-to-end behaviour.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import nbsnap


def test_top_level_symbols_resolve() -> None:
    """Pin the four documented embedding entry points."""

    assert callable(nbsnap.run_export)
    assert callable(nbsnap.run_import)
    assert isinstance(nbsnap.__version__, str)
    assert hasattr(nbsnap.Manifest, "write")
    assert hasattr(nbsnap.Manifest, "load")


def test_run_export_signature_matches_documented_shape() -> None:
    """``run_export(http, out_dir, *, scope=..., resume=...)``."""

    sig = inspect.signature(nbsnap.run_export)
    params = list(sig.parameters.values())
    assert params[0].name == "http"
    assert params[1].name == "out_dir"


def test_run_import_signature_matches_documented_shape() -> None:
    """``run_import(http, snapshot_dir, ...)``."""

    sig = inspect.signature(nbsnap.run_import)
    params = list(sig.parameters.values())
    assert params[0].name == "http"
    # The second positional may be `snapshot_dir`, `in_dir`, or
    # similar across the migration; pin only that it is present.
    assert len(params) >= 2


def test_manifest_roundtrip_via_public_surface(tmp_path: Path) -> None:
    """A user can write a Manifest and read it back through ``nbsnap``."""

    manifest = nbsnap.Manifest(
        source_url_hash="deadbeef0001",
        netbox_version="4.6.2",
        counts={"dcim.site": 1},
    )
    path = tmp_path / "manifest.json"
    manifest.write(path)

    loaded = nbsnap.Manifest.load(path)
    assert loaded.source_url_hash == "deadbeef0001"
    assert loaded.counts == {"dcim.site": 1}
