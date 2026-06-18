"""Tests for task #32: skip structurally incomplete records.

The rescue-10 post-fix run surfaced HTTP 400 errors like
`__all__: Must define A and B terminations when creating a
new cable.` These happen when an upstream resolver pre-pass
emptied both `a_terminations` and `b_terminations` because
the interfaces they pointed at failed to import. Sending a
cable POST with no terminations is meaningless; NetBox refuses
with an aggregate `__all__` error that hides the real issue.

`_record_is_structurally_incomplete` detects these cases at
the upsert boundary and `upsert` returns
`UpsertOutcome.SKIPPED` instead of attempting the POST. The
operator sees a clean `skipped: N` in the summary and the
audit log records the skipped row with a clear reason.

Four behaviours pinned here:

1. A cable body with both terminations present passes the
   precondition and proceeds to the normal POST path.
2. A cable body with only one termination is skipped with a
   clear reason message.
3. A cable body with neither termination is also skipped.
4. Content types other than `dcim.cable` are not affected;
   the precondition check returns None for them.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.upsert import (
    UpsertOutcome,
    _record_is_structurally_incomplete,
    upsert,
)
from nbsnap.natkey.registry import default as default_registry

# ---------------------------------------------------------------------------
# Precondition check
# ---------------------------------------------------------------------------


def test_cable_with_both_terminations_passes_precondition() -> None:
    """A cable body that carries both a_ and b_terminations
    passes through, the resolver placed real endpoints on
    each side."""

    body = {
        "a_terminations": [{"object_type": "dcim.interface", "object_id": 1}],
        "b_terminations": [{"object_type": "dcim.interface", "object_id": 2}],
        "type": "cat6",
    }
    assert _record_is_structurally_incomplete("dcim.cable", body) is None


def test_cable_with_only_a_terminations_is_incomplete() -> None:
    """One-ended cable, the b side dropped because its
    interface failed to import. Skip the row."""

    body = {
        "a_terminations": [{"object_type": "dcim.interface", "object_id": 1}],
        "type": "cat6",
    }
    reason = _record_is_structurally_incomplete("dcim.cable", body)
    assert reason is not None
    assert "no resolvable terminations" in reason


def test_cable_with_neither_termination_is_incomplete() -> None:
    """Both sides dropped, the cable has nothing to connect."""

    body = {"type": "cat6"}
    assert _record_is_structurally_incomplete("dcim.cable", body) is not None


def test_cable_with_empty_termination_list_is_incomplete() -> None:
    """An explicit `[]` is just as bad as a missing key."""

    body = {
        "a_terminations": [],
        "b_terminations": [{"object_type": "dcim.interface", "object_id": 2}],
    }
    assert _record_is_structurally_incomplete("dcim.cable", body) is not None


def test_non_cable_content_types_are_unaffected() -> None:
    """The precondition is wired only for dcim.cable today;
    everything else returns None so the upsert proceeds
    normally."""

    body_site = {"slug": "hall-d"}
    assert _record_is_structurally_incomplete("dcim.site", body_site) is None
    body_device = {"name": "d39a", "device_type": 1}
    assert _record_is_structurally_incomplete("dcim.device", body_device) is None


# ---------------------------------------------------------------------------
# upsert() returns SKIPPED with a useful message
# ---------------------------------------------------------------------------


def test_upsert_returns_skipped_for_unbuildable_cable() -> None:
    """End-to-end: the upsert path short-circuits via the
    precondition and returns UpsertOutcome.SKIPPED with the
    reason text. No HTTP call fires."""

    http = MagicMock()
    http.get_all.return_value = iter([])  # no destination data

    body = {
        "a_terminations": [{"object_type": "dcim.interface", "object_id": 1}],
        # b_terminations missing on purpose.
        "type": "cat6",
    }
    result = upsert(
        http,
        content_type="dcim.cable",
        natural_key=(("c1",),),
        body=body,
        index=NKIndex(),
        registry=default_registry(),
    )
    assert result.outcome is UpsertOutcome.SKIPPED
    assert "no resolvable terminations" in result.message
    # No POST was attempted, the precondition short-circuits.
    http.post.assert_not_called()


def test_upsert_skipped_outcome_carries_destination_id_none() -> None:
    """SKIPPED rows have no destination id, the audit
    consumers can rely on `result.destination_id is None` to
    distinguish them from CREATED/UPDATED."""

    http = MagicMock()
    http.get_all.return_value = iter([])
    result = upsert(
        http,
        content_type="dcim.cable",
        natural_key=(("c1",),),
        body={},
        index=NKIndex(),
        registry=default_registry(),
    )
    assert result.destination_id is None


def test_upsert_outcome_enum_includes_skipped() -> None:
    """The new enum value lives on the public surface so the
    CLI summary block and any external consumers can iterate
    every outcome including SKIPPED."""

    assert UpsertOutcome.SKIPPED.value == "skipped"
    assert UpsertOutcome.SKIPPED in set(UpsertOutcome)
