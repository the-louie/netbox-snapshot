"""ARCH-11b: ``nbsnap.import_`` exposes ``run_import`` and ``ResolveContext``."""

from __future__ import annotations


def test_public_api_re_exports() -> None:
    from nbsnap.import_ import ResolveContext, run_import

    assert callable(run_import)
    assert hasattr(ResolveContext, "__dataclass_fields__")


def test_public_all_is_exact() -> None:
    import nbsnap.import_

    assert set(nbsnap.import_.__all__) == {"ResolveContext", "run_import"}
