"""REFACTOR-03a/b tests for the BodyPreparer chain."""

from __future__ import annotations

from nbsnap.import_.body_preparer import BodyPreparer


def test_enum_dict_collapse_runs_first() -> None:
    """An enum-dict status becomes a flat string after prepare."""

    bp = BodyPreparer()
    out, coerced = bp.prepare(
        "dcim.site",
        {
            "name": "Hall-A",
            "status": {"value": "active", "label": "Active"},
        },
    )
    assert out["status"] == "active"
    assert coerced == ["status"]


def test_none_drop_when_opted_in() -> None:
    """With drop_nones=True, None values are removed from the
    body so NetBox's 'field may not be blank' refusals on a
    POST do not fire."""

    bp = BodyPreparer(drop_nones=True)
    out, _ = bp.prepare(
        "dcim.cable",
        {
            "type": "cat6",
            "profile": None,
            "length": None,
        },
    )
    assert "profile" not in out
    assert "length" not in out
    assert out["type"] == "cat6"


def test_none_drop_off_by_default_for_patch() -> None:
    """The PATCH path keeps `None` so a real null-out of a
    field still survives the chain."""

    bp = BodyPreparer()
    out, _ = bp.prepare("dcim.cable", {"profile": None})
    assert out["profile"] is None


def test_chain_handles_enum_dict_and_none_drop_together() -> None:
    """Both transforms apply when drop_nones=True."""

    bp = BodyPreparer(drop_nones=True)
    out, coerced = bp.prepare(
        "dcim.device",
        {
            "name": "d",
            "status": {"value": "active", "label": "Active"},
            "tenant": None,
        },
    )
    assert out["status"] == "active"
    assert "tenant" not in out
    assert coerced == ["status"]
