"""FEAT-36f tests for the sharpened exit-code contract.

Three cases the new `_compute_exit_code` distinguishes:

1. A clean run with only OUT_OF_SCOPE or DEFERRED_TO_PHASE2
   drops returns EXIT_OK.
2. A run with MISSING_FROM_SOURCE drops returns EXIT_ROW_FAILURES
   even when Phase-1 and Phase-2 themselves had no failures.
3. A run with Phase-2 PATCH failures returns EXIT_ROW_FAILURES
   even when Phase-1 succeeded.
"""

from __future__ import annotations

from collections import Counter
from unittest.mock import MagicMock

from nbsnap.import_.audit import Auditor, DropCategory, DropEvent
from nbsnap.import_.driver import ImportSummary
from nbsnap.import_.phase2 import Phase2Outcome, Phase2Summary
from nbsnap.import_.upsert import UpsertOutcome
from nbsnap.import_cli import (
    EXIT_OK,
    EXIT_PREFLIGHT_BLOCKED,
    EXIT_ROW_FAILURES,
    _compute_exit_code,
)
from nbsnap.schema.status import VersionSkew


def _summary(
    *,
    failures: list | None = None,
    audit_events: list[DropEvent] | None = None,
    phase2_failed: int = 0,
    blocking: bool = False,
    parse_errors: list[dict] | None = None,
) -> ImportSummary:
    """Build an `ImportSummary` shaped for the exit-code logic."""

    pre = MagicMock()
    pre.is_blocking.return_value = blocking
    pre.version_skew = VersionSkew.MINOR

    auditor = Auditor()
    for ev in audit_events or []:
        auditor.record(ev)

    p2 = Phase2Summary()
    if phase2_failed:
        p2.counts[Phase2Outcome.FAILED] = phase2_failed

    s = ImportSummary(preflight=pre)
    s.failures = failures or []
    s.auditor = auditor
    s.counts = Counter()
    s.phase2 = p2 if phase2_failed else None
    s.parse_errors = parse_errors or []
    return s


def _drop(category: DropCategory, target_nk=("x",)) -> DropEvent:
    return DropEvent(
        category=category,
        child_content_type="dcim.site",
        child_nk=("hall-d",),
        field_name="region",
        target_content_type="dcim.region",
        target_nk=target_nk,
    )


def test_clean_run_returns_ok() -> None:
    """No failures, no audit drops, returns EXIT_OK."""

    assert _compute_exit_code(_summary(), VersionSkew.MINOR) == EXIT_OK


def test_out_of_scope_drops_do_not_fail_the_run() -> None:
    """OUT_OF_SCOPE drops are documented behaviour; the network-
    only scope banner deliberately excludes them."""

    s = _summary(audit_events=[_drop(DropCategory.OUT_OF_SCOPE)])
    assert _compute_exit_code(s, VersionSkew.MINOR) == EXIT_OK


def test_deferred_drops_do_not_fail_the_run() -> None:
    """DEFERRED_TO_PHASE2 drops are evidence that the cycle
    breaker did its job, not evidence of an error."""

    s = _summary(audit_events=[_drop(DropCategory.DEFERRED_TO_PHASE2)])
    assert _compute_exit_code(s, VersionSkew.MINOR) == EXIT_OK


def test_missing_from_source_fails_the_run() -> None:
    """MISSING_FROM_SOURCE drops mean the source NetBox has
    a stale or broken reference; surface as a row failure."""

    s = _summary(audit_events=[_drop(DropCategory.MISSING_FROM_SOURCE)])
    assert _compute_exit_code(s, VersionSkew.MINOR) == EXIT_ROW_FAILURES


def test_phase2_failure_fails_the_run() -> None:
    """A Phase-2 PATCH failure surfaces via EXIT_ROW_FAILURES
    even when Phase-1 was clean."""

    assert _compute_exit_code(_summary(phase2_failed=2), VersionSkew.MINOR) == EXIT_ROW_FAILURES


def test_phase1_failure_fails_the_run() -> None:
    """Phase-1 upsert failures still drive EXIT_ROW_FAILURES."""

    fake_failure = MagicMock()
    fake_failure.outcome = UpsertOutcome.FAILED
    fake_failure.message = "HTTP 400"
    s = _summary(failures=[fake_failure])
    assert _compute_exit_code(s, VersionSkew.MINOR) == EXIT_ROW_FAILURES


def test_blocking_preflight_takes_precedence_over_failures() -> None:
    """When preflight blocked the run, return EXIT_PREFLIGHT_BLOCKED
    regardless of any in-band failures, the import never ran."""

    fake_failure = MagicMock()
    fake_failure.outcome = UpsertOutcome.FAILED
    fake_failure.message = "HTTP 400"
    s = _summary(failures=[fake_failure], blocking=True)
    assert _compute_exit_code(s, VersionSkew.MINOR) == EXIT_PREFLIGHT_BLOCKED


def test_mixed_audit_categories_only_missing_matters() -> None:
    """A run with a mix of categories fails only when at least
    one MISSING_FROM_SOURCE is present."""

    s = _summary(
        audit_events=[
            _drop(DropCategory.OUT_OF_SCOPE, ("a",)),
            _drop(DropCategory.DEFERRED_TO_PHASE2, ("b",)),
        ]
    )
    assert _compute_exit_code(s, VersionSkew.MINOR) == EXIT_OK
    s.auditor.record(_drop(DropCategory.MISSING_FROM_SOURCE, ("c",)))
    assert _compute_exit_code(s, VersionSkew.MINOR) == EXIT_ROW_FAILURES


def test_parse_errors_over_threshold_fails_run() -> None:
    """BUG-06: parse errors over `max_parse_errors` cause
    EXIT_ROW_FAILURES; under the threshold stays EXIT_OK."""

    one_err = [{"path": "x.jsonl", "lineno": 5, "message": "bad"}]
    s = _summary(parse_errors=one_err)
    # Default threshold (0) treats any parse error as failure.
    assert _compute_exit_code(s, VersionSkew.MINOR) == EXIT_ROW_FAILURES
    # Raising the threshold above the count keeps the run clean.
    assert _compute_exit_code(s, VersionSkew.MINOR, max_parse_errors=1) == EXIT_OK


def test_no_parse_errors_does_not_fail() -> None:
    """A clean run with zero parse errors stays OK at any threshold."""

    s = _summary()
    assert _compute_exit_code(s, VersionSkew.MINOR, max_parse_errors=0) == EXIT_OK


def test_max_skipped_global_threshold_fires() -> None:
    """FEAT-41: total SKIPPED above --max-skipped trips
    EXIT_SKIPPED_OVER_THRESHOLD (6)."""

    from nbsnap.import_cli import EXIT_SKIPPED_OVER_THRESHOLD

    s = _summary()
    s.skipped_by_ct = {"dcim.cable": {"no resolvable terminations": 4}}
    code = _compute_exit_code(s, VersionSkew.MINOR, max_skipped=3)
    assert code == EXIT_SKIPPED_OVER_THRESHOLD


def test_max_skipped_negative_disables_global_gate() -> None:
    """Default --max-skipped=-1 means unbounded; a SKIPPED
    cloud does not flip the exit code."""

    s = _summary()
    s.skipped_by_ct = {"ipam.ipaddress": {"duplicate IP": 100}}
    assert _compute_exit_code(s, VersionSkew.MINOR, max_skipped=-1) == EXIT_OK


def test_max_skipped_per_ct_fires_independently() -> None:
    """A per-content-type threshold trips even when the global
    threshold is comfortable."""

    from nbsnap.import_cli import EXIT_SKIPPED_OVER_THRESHOLD

    s = _summary()
    s.skipped_by_ct = {"ipam.ipaddress": {"duplicate IP": 19}}
    code = _compute_exit_code(
        s,
        VersionSkew.MINOR,
        max_skipped=-1,
        max_skipped_ct={"ipam.ipaddress": 5},
    )
    assert code == EXIT_SKIPPED_OVER_THRESHOLD


def test_max_skipped_per_ct_under_threshold_is_ok() -> None:
    """When the per-ct count fits, no exit-code flip."""

    s = _summary()
    s.skipped_by_ct = {"dcim.cable": {"no resolvable terminations": 4}}
    code = _compute_exit_code(
        s,
        VersionSkew.MINOR,
        max_skipped_ct={"dcim.cable": 10},
    )
    assert code == EXIT_OK


def test_strict_schema_blocks_when_drift_present() -> None:
    """FEAT-46c: --strict-schema turns any non-empty
    schema_drift into EXIT_PREFLIGHT_BLOCKED. Without the
    flag the drift is informational and the run completes."""

    from nbsnap.schema.diff import FieldDrift

    s = _summary()
    s.preflight.is_blocking.return_value = True  # mock to honour strict_schema
    s.preflight.schema_drift = [
        FieldDrift("dcim.site", "region", "dcim.region", "dcim.area"),
    ]
    code = _compute_exit_code(
        s,
        VersionSkew.MINOR,
        strict_schema=True,
    )
    assert code == EXIT_PREFLIGHT_BLOCKED


def test_bypass_used_exit_code() -> None:
    """FEAT-49: a clean run that consumed the enum-dict bypass
    AND recorded at least one BYPASS_COERCED event surfaces
    EXIT_BYPASS_USED instead of EXIT_OK."""

    from nbsnap.import_cli import EXIT_BYPASS_USED

    s = _summary(
        audit_events=[
            DropEvent(
                category=DropCategory.BYPASS_COERCED,
                child_content_type="dcim.site",
                child_nk=("hall-a",),
                field_name="status",
                target_content_type="dcim.site",
                target_nk=("hall-a",),
                message="coerced",
            ),
        ]
    )
    assert _compute_exit_code(s, VersionSkew.MINOR, allow_enum_dict_bypass=True) == EXIT_BYPASS_USED


def test_no_bypass_event_returns_ok_even_with_flag() -> None:
    """The exit code only flips to BYPASS_USED when the audit
    actually carries a BYPASS_COERCED event. A clean snapshot
    run with the flag set but no coercions returns OK."""

    s = _summary()
    assert _compute_exit_code(s, VersionSkew.MINOR, allow_enum_dict_bypass=True) == EXIT_OK
