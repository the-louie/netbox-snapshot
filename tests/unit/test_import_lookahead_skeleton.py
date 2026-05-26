"""FEAT-36b1 skeleton tests.

Three things pinned: DeferredFK is frozen (cycle detector
relies on hashability), two equal DeferredFKs compare equal
and hash the same (Phase-2 dedupe), MAX_DEPTH is the
documented value (so a bump shows up as a test diff).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from nbsnap.import_.lookahead import MAX_DEPTH, DeferredFK


def _sample() -> DeferredFK:
    return DeferredFK(
        child_content_type="dcim.device",
        child_nk=(("hall-d",), "d39a"),
        field_name="primary_ip4",
        target_content_type="ipam.ipaddress",
        target_nk=("172.16.1.10/24",),
    )


def test_deferred_fk_is_frozen() -> None:
    """Frozen dataclass: mutating an attribute raises."""

    entry = _sample()
    with pytest.raises(FrozenInstanceError):
        entry.field_name = "oob_ip"  # type: ignore[misc]


def test_deferred_fk_equality_and_hashing() -> None:
    """Two DeferredFKs with identical fields compare equal AND
    hash the same so a set-based dedupe works in Phase-2."""

    a = _sample()
    b = _sample()
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_max_depth_is_200() -> None:
    """The documented depth cap. A change here is a real signal,
    not a routine tweak, so we pin the literal value."""

    assert MAX_DEPTH == 200
