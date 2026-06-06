"""ARCH-11c: the top-level ``nbsnap`` package re-exports the four entry points."""

from __future__ import annotations


def test_top_level_re_exports_resolve() -> None:
    from nbsnap import Manifest, __version__, run_export, run_import

    assert callable(run_export)
    assert callable(run_import)
    assert hasattr(Manifest, "write")
    assert isinstance(__version__, str)


def test_top_level_all_is_exact() -> None:
    import nbsnap

    assert set(nbsnap.__all__) == {"Manifest", "__version__", "run_export", "run_import"}
