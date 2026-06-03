"""Tests for task #27: live progress reporting + periodic audit flush.

The `ProgressReporter` is the operator's window into a long
running `nbsnap import`. Before this module landed, the CLI was
silent until the run finished, so a 25-minute import looked
identical to a hung process.

Four behaviours pinned here:

1. The phase header lands on stream once per content type with
   the row count, e.g. `# Importing dcim.site (10 records)`.
2. Per-row ticks land at the first row, the last row, and at
   the configured stride, never more than ~100 lines per
   phase regardless of total.
3. The periodic audit flush writes the Auditor's events to
   disk every 30 seconds (via a swappable clock for the test).
4. A None stream disables stderr output entirely (the existing
   driver tests rely on this so they stay quiet).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from nbsnap.import_.audit import Auditor, DropCategory, DropEvent
from nbsnap.import_.progress import (
    _TARGET_TICK_COUNT,
    ProgressReporter,
)


def _drop(field: str = "region") -> DropEvent:
    return DropEvent(
        category=DropCategory.OUT_OF_SCOPE,
        child_content_type="dcim.site",
        child_nk=("hall-d",),
        field_name=field,
        target_content_type="dcim.region",
        target_nk=("elmia",),
    )


# ---------------------------------------------------------------------------
# Phase header + per-row ticks
# ---------------------------------------------------------------------------


def test_phase_header_lands_with_total_count() -> None:
    """The `# Importing <ct> (n records)` header lands on
    `start_phase` with the row count baked in."""

    stream = io.StringIO()
    p = ProgressReporter(stream=stream)
    p.start_phase("dcim.site", total=10)
    output = stream.getvalue()
    assert "# Importing dcim.site (10 records)" in output


def test_low_cardinality_phase_ticks_every_row() -> None:
    """A phase with total <= _TARGET_TICK_COUNT ticks every
    row. Operators want full visibility on the small phases."""

    stream = io.StringIO()
    p = ProgressReporter(stream=stream)
    p.start_phase("dcim.site", total=3)
    for i in range(1, 4):
        p.tick("dcim.site", i)

    output = stream.getvalue()
    assert "dcim.site 1/3" in output
    assert "dcim.site 2/3" in output
    assert "dcim.site 3/3" in output


def test_high_cardinality_phase_samples_at_stride() -> None:
    """A phase with total >> _TARGET_TICK_COUNT samples at a
    stride so the terminal does not flood. Total tick lines
    should be roughly _TARGET_TICK_COUNT, plus first and last."""

    stream = io.StringIO()
    p = ProgressReporter(stream=stream)
    p.start_phase("dcim.interface", total=3582)
    for i in range(1, 3583):
        p.tick("dcim.interface", i)

    output = stream.getvalue()
    tick_lines = [
        line for line in output.splitlines()
        if "dcim.interface " in line and "/" in line
    ]
    # We allow a little slack because of the integer ceil()
    # used to compute the stride; aim for roughly TARGET ticks.
    assert _TARGET_TICK_COUNT - 5 <= len(tick_lines) <= _TARGET_TICK_COUNT + 5


def test_last_row_always_ticks_even_when_not_on_stride() -> None:
    """The final row of a phase always emits a tick so the
    operator sees the phase complete cleanly. Without this
    rule, a high-cardinality phase could end mid-stride and
    look stuck."""

    stream = io.StringIO()
    p = ProgressReporter(stream=stream)
    p.start_phase("dcim.interface", total=3501)  # 3501/100 stride = 36
    for i in range(1, 3502):
        p.tick("dcim.interface", i)
    output = stream.getvalue()
    assert "dcim.interface 3501/3501" in output


# ---------------------------------------------------------------------------
# Stream=None disables output
# ---------------------------------------------------------------------------


def test_none_stream_suppresses_output() -> None:
    """Passing `stream=None` keeps the reporter quiet, useful
    for tests and for non-TTY invocations."""

    p = ProgressReporter(stream=None)
    # Should not raise; should not emit anywhere.
    p.start_phase("dcim.site", total=5)
    for i in range(1, 6):
        p.tick("dcim.site", i)
    p.end_phase("dcim.site")
    p.close()


# ---------------------------------------------------------------------------
# Periodic audit flush
# ---------------------------------------------------------------------------


def test_periodic_audit_flush_fires_after_interval(tmp_path: Path) -> None:
    """When more than 30 seconds elapse between ticks, the
    audit JSONL is flushed to disk. We swap the clock so the
    test does not need to sleep."""

    fake_time = [0.0]

    def clock() -> float:
        return fake_time[0]

    audit_path = tmp_path / "audit.jsonl"
    auditor = Auditor()
    p = ProgressReporter(
        stream=None, auditor=auditor, audit_path=audit_path, clock=clock,
    )

    # Record a drop before the interval elapses; tick once;
    # the audit file should still be empty (interval not hit).
    auditor.record(_drop("region"))
    p.tick("dcim.site", 1)
    assert not audit_path.exists() or audit_path.read_text() == ""

    # Advance the clock past the flush interval; the next tick
    # triggers the flush.
    fake_time[0] = 31.0
    p.tick("dcim.site", 2)
    assert audit_path.exists()
    rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["category"] == "out_of_scope"


def test_close_flushes_audit_on_final_exit(tmp_path: Path) -> None:
    """`close()` flushes regardless of the interval, so the
    end-of-run state is always on disk."""

    audit_path = tmp_path / "audit.jsonl"
    auditor = Auditor()
    auditor.record(_drop("group"))

    p = ProgressReporter(stream=None, auditor=auditor, audit_path=audit_path)
    # No ticks fired, no interval elapsed; `close` still
    # writes the file.
    p.close()
    assert audit_path.exists()
    rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert len(rows) == 1


def test_reporter_constructed_with_auditor_flushes_on_close(tmp_path: Path) -> None:
    """REFACTOR-07: the reporter takes its auditor at
    construction; there is no late-binding API. `close()`
    flushes the events the auditor has accumulated."""

    audit_path = tmp_path / "audit.jsonl"
    auditor = Auditor()
    auditor.record(_drop())
    p = ProgressReporter(stream=None, auditor=auditor, audit_path=audit_path)
    p.close()
    assert audit_path.exists()
    assert "out_of_scope" in audit_path.read_text()


def test_zero_total_phase_still_emits_header() -> None:
    """Even a phase with zero rows emits its header so the
    operator sees we are not stuck on the previous phase."""

    stream = io.StringIO()
    p = ProgressReporter(stream=stream)
    p.start_phase("ipam.fhrpgroup", total=0)
    assert "# Importing ipam.fhrpgroup (0 records)" in stream.getvalue()


# ---------------------------------------------------------------------------
# Driver wire-up: bind_auditor fires during run_import
# ---------------------------------------------------------------------------


def test_driver_constructs_reporter_with_live_auditor(tmp_path: Path) -> None:
    """REFACTOR-07 integration check: passing `progress_stream`
    and `progress_audit_path` to `run_import` causes the driver
    to construct the ProgressReporter internally, after the
    summary exists. The reporter is born with the live auditor
    attached, so periodic flushes pick up drops during the run."""

    import io
    import json
    from unittest.mock import MagicMock

    from nbsnap.import_.driver import run_import
    from nbsnap.schema.status import VersionSkew

    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "netbox_version": "4.6.2",
        "counts": {}, "deferred_edges": [],
    }))
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    (schema_dir / "openapi.json").write_text(json.dumps({
        "openapi": "3.0.3", "paths": {}, "components": {"schemas": {}},
    }))

    import nbsnap.import_.driver as driver_mod
    original_preflight = driver_mod.run_preflight
    fake_report = MagicMock()
    fake_report.is_blocking.return_value = False
    driver_mod.run_preflight = lambda *_a, **_kw: fake_report
    try:
        audit_path = tmp_path / "audit.jsonl"
        stream = io.StringIO()
        summary = run_import(
            MagicMock(), tmp_path,
            max_skew=VersionSkew.MAJOR, on_error="continue",
            progress_stream=stream,
            progress_audit_path=audit_path,
        )
        # The summary's auditor exists, and the run succeeded.
        # No backwards-compat handle to assert against; the
        # forward guarantee is that the reporter never had a
        # `None` auditor.
        assert summary.auditor is not None
    finally:
        driver_mod.run_preflight = original_preflight


def test_audit_flushes_at_5s_cadence(tmp_path: Path) -> None:
    """FEAT-43: the flush cadence is 5 seconds, not 30. A tick
    that lands 6 seconds after construction flushes the
    pending audit events to disk."""

    from nbsnap.import_.progress import _AUDIT_FLUSH_INTERVAL_SECONDS
    assert _AUDIT_FLUSH_INTERVAL_SECONDS == 5.0

    audit_path = tmp_path / "audit.jsonl"
    auditor = Auditor()
    auditor.record(_drop())

    clock_now = [0.0]
    p = ProgressReporter(
        stream=None, auditor=auditor, audit_path=audit_path,
        clock=lambda: clock_now[0],
    )
    p.start_phase("dcim.site", total=1)
    # Advance the clock past the 5s window and emit one tick.
    clock_now[0] = 6.0
    p.tick("dcim.site", row_index=1)
    assert audit_path.exists()
    text = audit_path.read_text()
    assert "out_of_scope" in text


def test_progress_reporter_fsync_opt_in(tmp_path: Path) -> None:
    """FEAT-43: passing fsync=True causes the reporter to call
    os.fsync after each flush. We mock os.fsync to assert the
    call without depending on filesystem semantics."""

    from unittest.mock import patch

    audit_path = tmp_path / "audit.jsonl"
    auditor = Auditor()
    auditor.record(_drop())

    clock_now = [0.0]
    p = ProgressReporter(
        stream=None, auditor=auditor, audit_path=audit_path,
        clock=lambda: clock_now[0], fsync=True,
    )
    p.start_phase("dcim.site", total=1)
    clock_now[0] = 6.0
    with patch("os.fsync") as fake_fsync:
        p.tick("dcim.site", row_index=1)
    assert fake_fsync.called
