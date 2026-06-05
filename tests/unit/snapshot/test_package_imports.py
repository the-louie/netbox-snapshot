"""ARCH-01a: the ``snapshot`` package imports cleanly.

The next sub-tickets (ARCH-01b..d) will add real contents. For now we
just lock the scaffolding: the package is importable and its public
surface starts empty so future imports cannot accidentally rely on a
leaky default state.
"""

from __future__ import annotations


def test_snapshot_package_is_importable() -> None:
    import nbsnap.snapshot  # noqa: F401, side-effect-free import is the assertion


def test_snapshot_public_surface_starts_empty() -> None:
    """ARCH-01a baseline: the public surface is empty.

    ARCH-01b/c/d will append entries to ``__all__`` as they migrate
    contracts in. Each of those sub-tickets is expected to update
    this assertion (or replace it with one that asserts the now-
    populated set). The assertion is here so the *initial* scaffold
    cannot ship with an accidental dependency on
    :mod:`nbsnap.export` leaking through.
    """

    import nbsnap.snapshot

    assert nbsnap.snapshot.__all__ == []
