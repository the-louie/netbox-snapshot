"""FEAT-50: 10%-boundary progress lines for `reset-destination`.

Operators running the rescue loop need a coarse-grained signal
that a long-running section (e.g. `dcim.interface` with several
thousand records) is making progress, not stuck on an HTTP retry
storm. The emitter prints one `<ct>: <k>/<N> (<p>%)` line every
time the cumulative count crosses a 10% threshold; a final
`100%` line closes the section so the start ("N records to
delete") and end lines pair up.

Edge cases pinned:

* N==0 prints the explicit `done` line so the section never
  closes silently.
* Small N (3) lands the first percentage line on the first row.
* `quiet=True` keeps the closing line but suppresses the
  intermediate boundaries.
"""

from __future__ import annotations

import io
import sys

from nbsnap.reset_cli import _ResetProgress


def _capture(callable_, *args, **kwargs) -> str:
    """Run `callable_` with stderr captured, return the captured text."""
    buf = io.StringIO()
    real = sys.stderr
    sys.stderr = buf
    try:
        callable_(*args, **kwargs)
    finally:
        sys.stderr = real
    return buf.getvalue()


def test_progress_emits_every_ten_percent_for_round_total() -> None:
    """A 100-row content type processed in 10 ticks of 10 must
    emit lines at 10%, 20%, ..., 90% during tick, then 100% at
    finish — no duplicates, no missed boundaries."""

    def drive() -> None:
        p = _ResetProgress("dcim.interface", 100)
        for _ in range(10):
            p.tick(10)
        p.finish()

    out = _capture(drive)
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines == [
        "  dcim.interface: 10/100 (10%)",
        "  dcim.interface: 20/100 (20%)",
        "  dcim.interface: 30/100 (30%)",
        "  dcim.interface: 40/100 (40%)",
        "  dcim.interface: 50/100 (50%)",
        "  dcim.interface: 60/100 (60%)",
        "  dcim.interface: 70/100 (70%)",
        "  dcim.interface: 80/100 (80%)",
        "  dcim.interface: 90/100 (90%)",
        "  dcim.interface: 100/100 (100%)",
    ]


def test_progress_zero_total_prints_done_line_only() -> None:
    """A content type with no rows must surface as a single
    explicit `done` line. Without it, the section reads as
    silent and the operator cannot tell it was even attempted."""

    def drive() -> None:
        p = _ResetProgress("dcim.frontport", 0)
        p.finish()

    out = _capture(drive)
    assert out == "  dcim.frontport: 0/0 (done)\n"


def test_progress_small_total_emits_only_closing_done_line() -> None:
    """N<10 suppresses per-percentage lines and emits a single
    `N/N (done)` closing line. Printing 10 boundaries for a
    3-row section is noise; the start ("N records to delete")
    and end lines are enough for a section the operator can
    read at a glance."""

    def drive() -> None:
        p = _ResetProgress("dcim.platform", 3)
        for _ in range(3):
            p.tick(1)
        p.finish()

    out = _capture(drive)
    assert out == "  dcim.platform: 3/3 (done)\n"


def test_progress_quiet_keeps_closing_line_suppresses_boundaries() -> None:
    """`--quiet` removes the per-boundary noise but the closing
    `100%` (or `done`) line stays so the section's start and
    end remain pairable in the log."""

    def drive() -> None:
        p = _ResetProgress("dcim.interface", 100, quiet=True)
        for _ in range(10):
            p.tick(10)
        p.finish()

    out = _capture(drive)
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines == ["  dcim.interface: 100/100 (100%)"]


def test_progress_single_large_tick_emits_every_crossed_boundary() -> None:
    """If one batch covers more than 10% of the total, every
    crossed boundary still surfaces. We do not collapse to the
    final boundary because the log would lose information that
    the run did pass through the lower thresholds."""

    def drive() -> None:
        p = _ResetProgress("dcim.cable", 50)
        # 30 ids in one tick covers 60% (10/20/30/40/50/60)
        p.tick(30)
        p.tick(20)
        p.finish()

    out = _capture(drive)
    lines = [line for line in out.splitlines() if line.strip()]
    # Six lines from the first tick (10% through 60%), three
    # more from the second (70% through 90%), then 100% at finish.
    assert lines == [
        "  dcim.cable: 30/50 (10%)",
        "  dcim.cable: 30/50 (20%)",
        "  dcim.cable: 30/50 (30%)",
        "  dcim.cable: 30/50 (40%)",
        "  dcim.cable: 30/50 (50%)",
        "  dcim.cable: 30/50 (60%)",
        "  dcim.cable: 50/50 (70%)",
        "  dcim.cable: 50/50 (80%)",
        "  dcim.cable: 50/50 (90%)",
        "  dcim.cable: 50/50 (100%)",
    ]
