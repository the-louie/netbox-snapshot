"""Tests for task #34: classify known POST failures as SKIPPED.

NetBox's HTTP 400 responses sometimes reflect destination
policy rather than a tool bug. The canonical case is
`ipam.iprange` POSTs refused with `Defined addresses overlap
with range X in VRF Y`, which fires when the destination has
NetBox's `ENFORCE_GLOBAL_UNIQUE` setting enabled but the
source allowed overlapping ranges. The import cannot fix the
destination's policy; the right behaviour is to mark the row
as SKIPPED so the operator sees a clean count in the audit
summary and can decide whether to relax the policy.

Three behaviours pinned here:

1. The classifier returns the explanation string when a known
   pattern matches the content type AND the error text.
2. A non-matching pattern leaves the failure as FAILED via a
   None return.
3. Through `upsert()`, a POST raising an exception that
   matches the classifier yields a SKIPPED result, not a
   FAILED one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.upsert import (
    UpsertOutcome,
    _classify_post_failure,
    upsert,
)
from nbsnap.natkey.registry import default as default_registry

# ---------------------------------------------------------------------------
# Classifier behaviour
# ---------------------------------------------------------------------------


def test_iprange_overlap_error_classified_as_skip() -> None:
    """The canonical case: an iprange POST refused because of
    overlap with an existing range returns an explanation
    that the audit log will carry."""

    body = (
        "HTTP 400 from POST ipam/ip-ranges/: "
        '{"__all__":["Defined addresses overlap with range '
        '92.33.40.1-47.255/21 in VRF None"]}'
    )
    explanation = _classify_post_failure("ipam.iprange", body)
    assert explanation is not None
    assert "overlap" in explanation.lower()
    assert "ENFORCE_GLOBAL_UNIQUE" in explanation


def test_ipaddress_duplicate_error_classified_as_skip() -> None:
    """An ipam.ipaddress POST refused because of a duplicate
    already on the destination returns an explanation that
    the audit log carries. Same family of issue as the
    iprange overlap; destination policy refusal rather than
    a tool bug."""

    body = (
        "HTTP 400 from POST ipam/ip-addresses/: "
        '{"address":["Duplicate IP address found in global table: '
        '172.16.255.13/24"]}'
    )
    explanation = _classify_post_failure("ipam.ipaddress", body)
    assert explanation is not None
    assert "duplicate" in explanation.lower()
    assert "ENFORCE_GLOBAL_UNIQUE" in explanation


def test_classifier_matches_content_type_strictly() -> None:
    """The same error text on a different content type is NOT
    classified as skip; the rule pair is (content_type, text)
    and both must match."""

    body = '{"__all__":["Defined addresses overlap with range X"]}'
    # The text is the iprange one but the content type is
    # different; classifier returns None.
    assert _classify_post_failure("dcim.device", body) is None
    assert _classify_post_failure("ipam.prefix", body) is None


def test_classifier_returns_none_for_unrelated_iprange_errors() -> None:
    """Other iprange failure modes (e.g. a real schema error)
    stay as FAILED. Only the specific overlap pattern triggers
    the skip outcome."""

    body = '{"status":["This field is required."]}'
    assert _classify_post_failure("ipam.iprange", body) is None


# ---------------------------------------------------------------------------
# Integration through upsert()
# ---------------------------------------------------------------------------


def test_upsert_reports_skipped_for_classified_overlap() -> None:
    """When `http.post` raises with the overlap text, the
    upsert path returns SKIPPED with the classifier's
    explanation, not the generic FAILED message."""

    http = MagicMock()
    http.get_all.return_value = iter([])  # empty index
    http.post.side_effect = RuntimeError(
        "POST ipam/ip-ranges/ -> HTTP 400: "
        '{"__all__":["Defined addresses overlap with range 92.33.40.1-47.255/21"]}'
    )

    result = upsert(
        http,
        content_type="ipam.iprange",
        natural_key=("92.33.40.137/26", "92.33.40.190/26"),
        body={
            "start_address": "92.33.40.137/26",
            "end_address": "92.33.40.190/26",
            "status": "active",
        },
        index=NKIndex(),
        registry=default_registry(),
    )
    assert result.outcome is UpsertOutcome.SKIPPED
    assert "overlap" in result.message.lower()
    # The destination id is None for skipped rows, by the
    # same contract as #32.
    assert result.destination_id is None


def test_upsert_keeps_failed_outcome_for_unclassified_errors() -> None:
    """A non-matching POST exception keeps the FAILED outcome
    so real bugs surface in the operator's failure count."""

    http = MagicMock()
    http.get_all.return_value = iter([])
    http.post.side_effect = RuntimeError("POST ipam/ip-ranges/ -> HTTP 500: server unavailable")

    result = upsert(
        http,
        content_type="ipam.iprange",
        natural_key=("x", "y"),
        body={"start_address": "1.0.0.1/24", "end_address": "1.0.0.10/24"},
        index=NKIndex(),
        registry=default_registry(),
    )
    assert result.outcome is UpsertOutcome.FAILED
    assert "server unavailable" in result.message


def test_classifier_table_does_not_match_substring_of_unrelated_text() -> None:
    """The substring is specific enough that an unrelated
    error message containing the words 'Defined' or 'overlap'
    in a different context would not falsely trigger. We
    match against `Defined addresses overlap` together."""

    body = "The user-defined timer overlaps the timeout window."
    assert _classify_post_failure("ipam.iprange", body) is None


# ---------------------------------------------------------------------------
# BUG-05 regression: reworded NetBox messages and near-miss detection
# ---------------------------------------------------------------------------


def test_reworded_iprange_overlap_still_matches() -> None:
    """BUG-05: a NetBox release that inserts 'the' or rewords
    slightly still trips the regex matcher. Before the
    structural shift, this would silently revert to FAILED."""

    msg = "addresses overlap with the range 10.0.0.0/24"
    assert _classify_post_failure("ipam.iprange", msg) is not None


def test_reworded_ipaddress_duplicate_still_matches() -> None:
    """BUG-05: 'Duplicate IP detected' instead of the canonical
    'Duplicate IP address found' still classifies correctly."""

    msg = "Duplicate IP detected at 192.168.1.1/24"
    assert _classify_post_failure("ipam.ipaddress", msg) is not None


def test_near_miss_logs_info_and_returns_none(caplog) -> None:
    """BUG-05 near-miss detector: if the error contains the
    keyword set but the structural regex fails, log INFO so a
    maintainer notices NetBox drifted."""

    import logging

    msg = "wrap of the addresses showed overlap but not against any range"
    with caplog.at_level(logging.INFO, logger="nbsnap.import_.upsert"):
        result = _classify_post_failure("ipam.iprange", msg)
    assert result is None
    assert any("near miss" in m.lower() for m in caplog.messages)
