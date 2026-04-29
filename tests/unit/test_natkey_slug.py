"""FEAT-09a slug resolver tests."""

from __future__ import annotations

import pytest

from nbsnap.natkey.registry import default
from nbsnap.natkey.resolver import resolve


def test_slug_resolves_single_field() -> None:
    reg = default()
    assert resolve(reg, "dcim.site", {"slug": "hall-d"}) == ("hall-d",)


def test_slug_empty_value_raises() -> None:
    reg = default()
    with pytest.raises(ValueError):
        resolve(reg, "dcim.site", {"slug": ""})


def test_tag_uses_slug_strategy() -> None:
    reg = default()
    assert resolve(reg, "extras.tag", {"slug": "iot"}) == ("iot",)
