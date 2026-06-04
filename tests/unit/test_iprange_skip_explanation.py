"""BUG-14: pin the corrected `ipam.iprange` skip explanation.

The earlier explanation falsely told operators they could clear
iprange overlaps by setting `ENFORCE_GLOBAL_UNIQUE = False` on
the destination. Rescue-13 disproved that: with the flag off,
NetBox still rejected all 86 overlapping ranges because the
check lives in `IPRange.clean()` and has no runtime toggle.

These tests are regression guards against the old text creeping
back into `_POST_FAILURE_SKIP_PATTERNS` while keeping the
operator-facing "overlap" keyword present so a grep on the
symptom still surfaces this skip class.
"""

from __future__ import annotations

from nbsnap.import_.upsert import _classify_post_failure


_NETBOX_OVERLAP_ERROR = (
    "Addresses overlap with the range "
    "92.33.40.137/26-92.33.40.190/26"
)


def test_iprange_overlap_is_classified_as_skipped() -> None:
    """The classifier still returns a skip explanation for the
    canonical NetBox overlap text — without this, the operator
    sees a generic FAILED instead of a SKIPPED."""

    explanation = _classify_post_failure(
        "ipam.iprange", _NETBOX_OVERLAP_ERROR,
    )
    assert explanation is not None


def test_iprange_explanation_does_not_blame_enforce_global_unique() -> None:
    """Regression guard for BUG-14. The previous text blamed
    `ENFORCE_GLOBAL_UNIQUE`, which is wrong — that setting only
    governs prefixes and IP addresses per NetBox docs and was
    proven irrelevant for ranges in rescue-13."""

    explanation = _classify_post_failure(
        "ipam.iprange", _NETBOX_OVERLAP_ERROR,
    )
    assert explanation is not None
    assert "ENFORCE_GLOBAL_UNIQUE" not in explanation or (
        "NOT governed by ENFORCE_GLOBAL_UNIQUE" in explanation
    ), (
        "BUG-14 regression: ipam.iprange explanation must not "
        "claim ENFORCE_GLOBAL_UNIQUE controls overlap; if it "
        "mentions the flag at all it must be to disclaim it."
    )


def test_iprange_explanation_still_mentions_overlap() -> None:
    """The operator-facing keyword `overlap` must survive the
    rewrite so a grep on the symptom still finds this skip
    class in audit summaries and downstream tooling."""

    explanation = _classify_post_failure(
        "ipam.iprange", _NETBOX_OVERLAP_ERROR,
    )
    assert explanation is not None
    assert "overlap" in explanation.lower()


def test_iprange_explanation_points_at_source_side_remediation() -> None:
    """The new text must point the operator at a remediation
    that actually works: either remove the row at the source or
    move one range to a different VRF. Without this the operator
    has no actionable path and may waste another iteration
    looking for a destination toggle that does not exist."""

    explanation = _classify_post_failure(
        "ipam.iprange", _NETBOX_OVERLAP_ERROR,
    )
    assert explanation is not None
    lower = explanation.lower()
    assert "source" in lower or "vrf" in lower, (
        "explanation must point at source-side remediation "
        "(remove the overlapping row at source, or assign a "
        "different VRF) since destination-side toggles do not "
        "clear this skip class."
    )
