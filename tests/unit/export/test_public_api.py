"""ARCH-11a: ``nbsnap.export`` exposes ``run_export`` and ``Manifest``."""

from __future__ import annotations


def test_public_api_re_exports() -> None:
    from nbsnap.export import Manifest, run_export

    assert callable(run_export)
    # Manifest is a dataclass type, the class object is enough here.
    assert hasattr(Manifest, "write")
    assert hasattr(Manifest, "load")


def test_public_all_is_exact() -> None:
    """``__all__`` exactly lists the public symbols, no accidents."""

    import nbsnap.export

    assert set(nbsnap.export.__all__) == {"Manifest", "run_export"}
