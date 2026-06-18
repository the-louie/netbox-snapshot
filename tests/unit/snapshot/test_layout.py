"""ARCH-01c: :mod:`nbsnap.snapshot.layout` behaviour tests.

The layout map is a contract that both the export side and the
import side rely on. Three behaviours need to be pinned:

1. Every key follows the documented ``<app>/<plural>.jsonl`` shape.
2. The known content types resolve through :func:`relative_path` to
   the same string as a direct dict lookup (no silent normalisation).
3. The legacy import path under :mod:`nbsnap.export.writer` resolves
   to the same objects (re-export, not a copy).

The hard-failure-on-unknown-content-type behaviour (ARCH-08a) is
pinned by ``tests/unit/snapshot/test_layout_unknown.py``; this file
covers the *known* content types only.
"""

from __future__ import annotations

import pytest

from nbsnap.snapshot.layout import CONTENT_TYPE_FILES, relative_path


def test_every_path_has_jsonl_suffix_and_two_components() -> None:
    for content_type, path in CONTENT_TYPE_FILES.items():
        assert path.endswith(".jsonl"), f"{content_type!r} maps to {path!r}, expected .jsonl"
        head, _, _ = path.partition("/")
        assert head, f"{content_type!r} maps to {path!r}, expected app/<plural>"


@pytest.mark.parametrize(
    "content_type",
    sorted(CONTENT_TYPE_FILES.keys()),
)
def test_relative_path_matches_dict_for_known_content_types(content_type: str) -> None:
    assert relative_path(content_type) == CONTENT_TYPE_FILES[content_type]


def test_legacy_writer_no_longer_re_exports_content_type_files() -> None:
    """ARCH-01f removed the ``CONTENT_TYPE_FILES`` re-export.

    The writer module still uses :func:`relative_path` internally
    (imported from ``nbsnap.snapshot.layout``), so the name is
    still resolvable as a module attribute, but the bigger map is
    not exported any more. Pin the deletion so the shim cannot
    creep back.
    """

    from nbsnap.export import writer as legacy

    assert not hasattr(legacy, "CONTENT_TYPE_FILES")
