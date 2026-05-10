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


def test_cable_termination_with_rewritten_natural_key() -> None:
    """After the FK rewriter runs, terminations carry object_natural_key
    instead of object_id; the cable NK must use the NK tuple, not None.
    """

    reg = default()
    rewritten_record = {
        "a_terminations": [
            {
                "object_type": "dcim.interface",
                "object_natural_key": [["d"], "INFRA-A", "Gi0/2"],
            }
        ],
        "b_terminations": [
            {
                "object_type": "dcim.interface",
                "object_natural_key": [["d"], "D-STREAM-STAGE-SW", "ge-0/0/0"],
            }
        ],
    }
    nk = resolve(reg, "dcim.cable", rewritten_record)
    # Pair sorted lexically, no None slot anywhere.
    flat = repr(nk)
    assert "None" not in flat
    assert "INFRA-A" in flat and "D-STREAM-STAGE-SW" in flat


def test_polymorphic_id_in_composite_nk_uses_target_nk() -> None:
    """ipam.ipaddress NK substitutes assigned_object_id with the target NK."""

    from nbsnap.natkey.model import NKField, NKRegistry, NKSpec, Strategy
    from nbsnap.natkey.resolver import resolve as resolve_fn

    # Build a small registry: interface uses a slug-like NK, ipaddress
    # uses (address, assigned_object_type, assigned_object_id).
    r = NKRegistry()
    r.register(NKSpec("dcim.interface", Strategy.SLUG, (NKField("name"),)))
    r.register(
        NKSpec(
            "ipam.ipaddress",
            Strategy.COMPOSITE,
            (
                NKField("address"),
                NKField("assigned_object_type"),
                NKField("assigned_object_id"),
            ),
        )
    )

    record = {
        "address": "10.0.0.1/24",
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": 99,
        "assigned_object": {"id": 99, "name": "Gi0/2"},
    }
    nk = resolve_fn(r, "ipam.ipaddress", record)
    # The third slot should be the interface's NK tuple, not the bare 99.
    assert nk == ("10.0.0.1/24", "dcim.interface", ("Gi0/2",))
