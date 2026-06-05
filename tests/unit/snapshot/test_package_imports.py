"""ARCH-01a: the ``snapshot`` package imports cleanly.

The next sub-tickets (ARCH-01b..d) will add real contents. For now we
just lock the scaffolding: the package is importable and its public
surface starts empty so future imports cannot accidentally rely on a
leaky default state.
"""

from __future__ import annotations


def test_snapshot_package_is_importable() -> None:
    import nbsnap.snapshot  # noqa: F401, side-effect-free import is the assertion


def test_snapshot_public_surface_contains_manifest() -> None:
    """ARCH-01b: ``Manifest`` and friends are re-exported at package root.

    Tracks the contract that the package owns. ARCH-01c/d will
    append further entries (``CONTENT_TYPE_FILES``, ``relative_path``,
    ``collapse_enum_dict``); update this set then.
    """

    import nbsnap.snapshot

    expected = {
        "CONTENT_TYPE_FILES",
        "MANIFEST_FILENAME",
        "Manifest",
        "SOURCE_URL_HASH_LENGTH",
        "compute_source_url_hash",
        "relative_path",
    }
    assert set(nbsnap.snapshot.__all__) == expected
