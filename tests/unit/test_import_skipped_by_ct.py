"""FEAT-40 tests for per-content-type SKIPPED breakdown.

The driver accumulates SKIPPED outcomes into
`ImportSummary.skipped_by_ct[ct][reason]`. The CLI renders
that breakdown under the `skipped: N` line so an operator
sees which content type lost rows and to what reason group.
"""

from __future__ import annotations

from nbsnap.import_.driver import ImportSummary, _skip_reason_group
from nbsnap.import_.preflight import PreflightReport


def test_skip_reason_group_collapses_canonical_messages() -> None:
    """The grouping helper strips the parenthetical detail and
    keeps the leading short reason, so per-record variation
    does not explode the per-ct buckets."""

    cases = {
        "no resolvable terminations": "no resolvable terminations",
        "no resolvable terminations: a- side missing": "no resolvable terminations",
        "duplicate IP in global table (172.16.1.10/24)": "duplicate IP in global table",
        "overlap with existing range": "overlap with existing range",
        "": "other",
    }
    for inp, expected in cases.items():
        assert _skip_reason_group(inp) == expected


def test_summary_initialises_empty_breakdown() -> None:
    """A fresh ImportSummary starts with an empty per-ct dict;
    no spurious entries appear before the first SKIPPED row."""

    s = ImportSummary(preflight=PreflightReport())
    assert s.skipped_by_ct == {}
