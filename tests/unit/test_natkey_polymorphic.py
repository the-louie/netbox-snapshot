"""FEAT-09c polymorphic-set resolver tests for Cable terminations."""

from __future__ import annotations

from nbsnap.natkey.registry import default
from nbsnap.natkey.resolver import resolve


def _cable(a_id: int, b_id: int) -> dict:
    return {
        "a_terminations": [{"object_type": "dcim.interface", "object_id": a_id}],
        "b_terminations": [{"object_type": "dcim.interface", "object_id": b_id}],
    }


def test_cable_swapping_a_and_b_produces_same_nk() -> None:
    """Cable NK is the set of terminations, order does not matter."""

    reg = default()
    nk_one = resolve(reg, "dcim.cable", _cable(7, 9))
    nk_two = resolve(reg, "dcim.cable", _cable(9, 7))
    assert nk_one == nk_two


def test_cable_with_multiple_terminations_each_side() -> None:
    """Multi-termination cables (LAGs) NK consider every endpoint."""

    reg = default()
    record = {
        "a_terminations": [
            {"object_type": "dcim.interface", "object_id": 1},
            {"object_type": "dcim.interface", "object_id": 2},
        ],
        "b_terminations": [{"object_type": "dcim.interface", "object_id": 3}],
    }
    nk = resolve(reg, "dcim.cable", record)
    # Two ends; one end has two terminations sorted ascending.
    assert nk == (
        (("dcim.interface", 1), ("dcim.interface", 2)),
        (("dcim.interface", 3),),
    )
