"""Tests for task #26: drop None-valued fields on POST.

NetBox refuses certain fields with HTTP 400 `may not be blank`
when the create body explicitly carries `null`. The headline
case is `dcim.cable.profile`, which is documented nullable in
the schema but rejected by the write validator. Dropping the
key from the body tells NetBox to use the default.

Two behaviours pinned here:

1. POST coerce drops keys whose value is `None` (the new
   default, controlled by `drop_nones=True`).
2. PATCH coerce KEEPS `None` values so a legitimate clear-
   to-null update survives (`drop_nones=False`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.upsert import (
    UpsertOutcome,
    _coerce_body_for_write,
    upsert,
)
from nbsnap.natkey.registry import default as default_registry


# ---------------------------------------------------------------------------
# Direct tests on the helper
# ---------------------------------------------------------------------------


def test_coerce_post_path_drops_none_values() -> None:
    """With `drop_nones=True` (the POST default), keys whose
    value is `None` are removed from the output."""

    body = {
        "name": "Cable-1",
        "type": "cat6",
        "profile": None,   # the canonical bug case
        "length": None,
    }
    out = _coerce_body_for_write(body, drop_nones=True)
    assert "profile" not in out
    assert "length" not in out
    # Non-None fields survive.
    assert out["name"] == "Cable-1"
    assert out["type"] == "cat6"


def test_coerce_patch_path_keeps_none_values() -> None:
    """With `drop_nones=False` (the PATCH default), `None`
    survives because a PATCH that wants to clear a field needs
    the explicit null."""

    body = {"name": "Cable-1", "profile": None}
    out = _coerce_body_for_write(body, drop_nones=False)
    assert out["profile"] is None


def test_coerce_default_keeps_nones_for_backwards_compat() -> None:
    """When the caller does not specify `drop_nones`, default
    is False so existing callers behave identically to the
    pre-fix code."""

    out = _coerce_body_for_write({"profile": None, "name": "x"})
    assert out["profile"] is None


def test_coerce_enum_dict_collapse_still_runs_alongside_none_drop() -> None:
    """The two transforms are independent and both apply when
    `drop_nones=True`. An enum-dict-shaped value collapses to
    its bare value, then survives the None check."""

    body = {
        "name": "x",
        "status": {"value": "active", "label": "Active"},
        "profile": None,
    }
    out = _coerce_body_for_write(body, drop_nones=True)
    assert out["status"] == "active"
    assert "profile" not in out


# ---------------------------------------------------------------------------
# Integration via upsert(): POST drops nulls, PATCH does not
# ---------------------------------------------------------------------------


def test_upsert_post_path_strips_null_profile() -> None:
    """Confirm the POST call sent by `upsert` does NOT carry
    `profile` when the input body had `profile: None`.

    The test body now also carries valid terminations because
    task #32 added a precondition that skips cables without
    them; we keep the cable shape to exercise the same code
    path the bug actually hit (cable.profile is the canonical
    rejection field, see task #26)."""

    http = MagicMock()
    http.get_all.return_value = iter([])  # empty index
    http.post.return_value = {"id": 1}

    result = upsert(
        http,
        content_type="dcim.cable",
        natural_key=("c1",),
        body={
            "profile": None,
            "type": "cat6",
            "status": "connected",
            "a_terminations": [{"object_type": "dcim.interface", "object_id": 1}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": 2}],
        },
        index=NKIndex(),
        registry=default_registry(),
    )
    assert result.outcome is UpsertOutcome.CREATED

    # The body passed to http.post must NOT include `profile`.
    sent_body = http.post.call_args.args[1]
    assert "profile" not in sent_body
    assert sent_body["type"] == "cat6"
    assert sent_body["status"] == "connected"
