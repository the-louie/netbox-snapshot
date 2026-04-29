"""FEAT-09b composite resolver tests, including parent recursion."""

from __future__ import annotations

from nbsnap.natkey.registry import default
from nbsnap.natkey.resolver import resolve


def test_composite_with_nested_parent_record() -> None:
    """Site nested as a dict, the resolver descends into its slug."""

    reg = default()
    nk = resolve(
        reg,
        "dcim.location",
        {"site": {"id": 1, "slug": "hall-d"}, "slug": "the-forge"},
    )
    assert nk == (("hall-d",), "the-forge")


def test_composite_with_brief_parent_id_uses_lookup() -> None:
    """Site referenced as a bare int, the lookup table supplies the slug."""

    reg = default()
    parent_lookup = {("dcim.site", 1): {"slug": "hall-d"}}
    nk = resolve(
        reg,
        "dcim.location",
        {"site": 1, "slug": "the-forge"},
        parent_lookup=parent_lookup,
    )
    assert nk == (("hall-d",), "the-forge")


def test_composite_device_uses_site_and_name() -> None:
    """Device NK is (site, name) per the renderer-minimum registry."""

    reg = default()
    nk = resolve(
        reg,
        "dcim.device",
        {"site": {"id": 2, "slug": "hall-d"}, "name": "d39a"},
    )
    assert nk == (("hall-d",), "d39a")
