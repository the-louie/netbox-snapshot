"""Live progress reporting and periodic audit flush for `nbsnap import`.

Before this module landed, `nbsnap import` was silent during
execution: the operator saw nothing on stderr until the run
finished and the summary block wrote. For a 5000-row snapshot
that meant a 25-minute black box. If the import was stuck,
slow, or about to crash, the operator could not tell.

`ProgressReporter` emits two kinds of stderr lines:

* One header per content-type phase, with the row count:
  `# Importing dcim.site (10 records)`
* Sampled per-row progress lines (every Nth row, plus the
  first and the last) so high-cardinality content types like
  `dcim.interface` (thousands of rows per import) do not
  flood the terminal but the operator still sees steady
  motion: `#   dcim.interface 1/3582 ... #   dcim.interface
  100/3582 ...`

It also schedules a periodic audit JSONL flush so a crashed
import still leaves diagnostic state on disk. The flush
cadence is a time-based budget (default every 30 seconds),
small enough that even a hard-killed import surfaces 90 % of
its drops.

The class is opt-in via the driver: pass `progress=None` to
disable (the existing test suite does this so the tests stay
quiet) or `ProgressReporter(stream=sys.stderr)` for the live
output mode the CLI uses.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from nbsnap.import_.audit import Auditor


# Target sample resolution for the per-row tick line. For each
# content type we aim for roughly this many tick lines, so a
# 100-row phase emits every row, a 5000-row phase emits roughly
# every 50th. The constant trades terminal noise against
# update granularity.
_TARGET_TICK_COUNT = 100

# How often (in seconds) to flush the audit JSONL to disk. The
# trade-off is the size of the diagnostic gap on a crash. 30
# seconds is short enough to keep the gap small and long enough
# to avoid hammering the disk on large imports.
# FEAT-43: cadence lowered from 30s -> 5s so a hard kill
# (OOMKill, deploy restart, SIGTERM) loses at most ~5s of audit
# events. Append-only JSONL writes are cheap enough that the
# tighter cadence does not noticeably affect throughput.
_AUDIT_FLUSH_INTERVAL_SECONDS = 5.0


class ProgressReporter:
    """Emits per-content-type progress lines and flushes the
    audit JSONL on a periodic cadence.

    Usage from the driver:

        progress = ProgressReporter(stream=sys.stderr,
                                    auditor=summary.auditor,
                                    audit_path=audit_path)
        for ct in content_types:
            rows = list(read_jsonl_for(ct))
            progress.start_phase(ct, total=len(rows))
            for i, row in enumerate(rows, start=1):
                upsert(...)
                progress.tick(ct, i)
            progress.end_phase(ct)
        progress.close()

    Tests can substitute a list-collector stream and a None
    auditor to assert the emitted lines without I/O.
    """

    def __init__(
        self,
        *,
        stream: IO[str] | None = None,
        auditor: Auditor | None = None,
        audit_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        fsync: bool = False,
        wallclock: Callable[[], datetime] | None = None,
        show_timestamps: bool = True,
    ) -> None:
        # `stream is None` -> progress lines suppressed entirely.
        # This lets the driver pass `progress=None` to existing
        # tests without polluting their captured stderr.
        self._stream = stream
        self._auditor = auditor
        self._audit_path = audit_path
        self._clock = clock
        # FEAT-43: opt-in os.fsync() after every write so the
        # audit JSONL survives a kernel-level kill (e.g.
        # OOMKill, machine reset). The default is False because
        # fsync adds a measurable per-write cost on container
        # filesystems and the 5s flush cadence is usually
        # enough on its own.
        self._fsync = fsync

        # Per-phase state. Reset at start_phase().
        self._current_total = 0
        self._current_tick_every = 1

        # Audit-flush timer.
        self._last_flush_at = self._clock()

        # FEAT-44: per-phase timing state. Reset on
        # `start_phase`. `_phase_started_at` carries the
        # monotonic timestamp at the start; `_phase_warned_rate`
        # prevents the rate-degradation warning from firing
        # more than once per phase.
        self._phase_started_at: float = 0.0
        self._phase_warned_rate: bool = False
        # Wall-clock provider used for prefix timestamps. The
        # default is `datetime.now`; tests substitute a fixed
        # clock via the constructor.
        self._wallclock: Callable[[], datetime] = datetime.now
        if wallclock is not None:
            self._wallclock = wallclock
        self._show_timestamps = show_timestamps

    # ------------------------------------------------------------------
    # Phase boundaries
    # ------------------------------------------------------------------

    def start_phase(self, content_type: str, *, total: int) -> None:
        """Announce the start of a content-type phase.

        `total` is the row count for the phase. The reporter
        uses it to pick the per-row sample stride so the tick
        output stays under `_TARGET_TICK_COUNT` lines per
        phase.

        A zero total still emits the header (so the operator
        sees we are not stalled on the previous phase) but no
        per-row ticks fire.
        """

        self._current_total = total
        # ceil so even a tiny `total` produces at least one tick
        # at row 1. For total <= TARGET we tick every row.
        self._current_tick_every = max(
            1, math.ceil(total / _TARGET_TICK_COUNT)
        )
        self._phase_started_at = self._clock()
        self._phase_warned_rate = False
        self._write(f"# Importing {content_type} ({total} records)\n")

    def end_phase(self, content_type: str) -> None:
        """Emit the FEAT-44 per-phase trailer with elapsed time
        and average throughput. Skipped when total is zero (no
        records means no meaningful rate)."""

        if self._current_total <= 0:
            return
        elapsed = max(0.0, self._clock() - self._phase_started_at)
        rate = (
            self._current_total / elapsed if elapsed > 0 else 0.0
        )
        # Compact human duration. Prefer Hh Mm Ss, then Mm Ss,
        # then Ss for short phases.
        secs = int(elapsed)
        if secs >= 3600:
            duration = f"{secs // 3600}h{(secs % 3600) // 60:02d}m{secs % 60:02d}s"
        elif secs >= 60:
            duration = f"{secs // 60}m{secs % 60:02d}s"
        else:
            duration = f"{secs}s"
        self._write(
            f"# Phase {content_type} complete: "
            f"{self._current_total} records in {duration} "
            f"({rate:.2f}/s)\n"
        )

    # ------------------------------------------------------------------
    # Per-row tick
    # ------------------------------------------------------------------

    def tick(self, content_type: str, row_index: int) -> None:
        """Emit a per-row progress line when the row index hits
        the configured stride, the first row, OR the last row.

        Also runs the periodic audit flush so a hard-killed
        import leaves recent drops on disk.
        """

        self._maybe_flush_audit()

        if self._stream is None:
            return

        is_first = row_index == 1
        is_last = row_index == self._current_total
        is_stride_hit = (
            self._current_tick_every > 0
            and row_index % self._current_tick_every == 0
        )
        if not (is_first or is_last or is_stride_hit):
            return

        self._write(
            f"#   {content_type} {row_index}/{self._current_total}\n"
        )

    # ------------------------------------------------------------------
    # Audit flush
    # ------------------------------------------------------------------

    def _maybe_flush_audit(self) -> None:
        """Flush the audit JSONL if more than the configured
        interval has passed since the last flush.

        Cheap when called frequently because we check the clock
        first and only touch disk when the budget elapses.
        """

        if self._auditor is None or self._audit_path is None:
            return
        now = self._clock()
        if now - self._last_flush_at < _AUDIT_FLUSH_INTERVAL_SECONDS:
            return
        self._auditor.write_jsonl(self._audit_path)
        if self._fsync:
            self._fsync_audit_path()
        self._last_flush_at = now

    def _fsync_audit_path(self) -> None:
        """Force the JSONL bytes onto stable storage.

        Opens the file read-only, fsyncs the fd, closes. Cheap
        enough at the 5s cadence; the operator opts into this
        when their host filesystem cannot be trusted to flush
        on its own before a hard kill.
        """
        import os
        if self._audit_path is None or not self._audit_path.exists():
            return
        with self._audit_path.open("rb") as fp:
            os.fsync(fp.fileno())


    def close(self) -> None:
        """Final audit flush, called at the end of `run_import`.

        Guarantees the on-disk audit reflects every drop, even
        when the periodic flush interval would not have fired
        on its own at end-of-run.
        """

        if self._auditor is not None and self._audit_path is not None:
            self._auditor.write_jsonl(self._audit_path)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write(self, line: str) -> None:
        """Emit one line to the configured stream, flushing
        immediately so a tee pipe does not buffer the output.

        FEAT-44: prepend an HH:MM:SS timestamp when timestamps
        are enabled. Lines that start with `# ` (phase markers)
        get the timestamp inside the marker so the operator's
        eye still finds the `#` quickly.
        """

        if self._stream is None:
            return
        if self._show_timestamps:
            stamp = self._wallclock().strftime("%H:%M:%S")
            if line.startswith("#"):
                line = f"# [{stamp}]{line[1:]}"
            else:
                line = f"[{stamp}] {line}"
        self._stream.write(line)
        self._stream.flush()


# Note: `_TARGET_TICK_COUNT` and `_AUDIT_FLUSH_INTERVAL_SECONDS`
# above are deliberately tunable module-private constants. The
# tests reach in to assert stride behaviour; future operators
# who want a chattier or quieter cadence can adjust them in one
# place. Keep them private (`_`-prefixed) so external callers
# do not depend on the exact values.
