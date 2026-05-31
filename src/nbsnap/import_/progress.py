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
  `dcim.interface` (3582 rows in the rescue-10 snapshot) do
  not flood the terminal but the operator still sees steady
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
_AUDIT_FLUSH_INTERVAL_SECONDS = 30.0


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
    ) -> None:
        # `stream is None` -> progress lines suppressed entirely.
        # This lets the driver pass `progress=None` to existing
        # tests without polluting their captured stderr.
        self._stream = stream
        self._auditor = auditor
        self._audit_path = audit_path
        self._clock = clock

        # Per-phase state. Reset at start_phase().
        self._current_total = 0
        self._current_tick_every = 1

        # Audit-flush timer.
        self._last_flush_at = self._clock()

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
        self._write(f"# Importing {content_type} ({total} records)\n")

    def end_phase(self, content_type: str) -> None:
        """Mark the end of a phase. Currently a no-op for output,
        kept on the surface so the driver call sites stay
        symmetric and a future "phase done in X seconds" line
        has a place to land."""

        # Reserved for future per-phase timing. No-op today.
        _ = content_type

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
        self._last_flush_at = now

    def bind_auditor(self, auditor: Auditor) -> None:
        """Attach an `Auditor` after construction.

        The CLI builds the reporter before `run_import` runs,
        but the auditor lives on the summary that `run_import`
        creates. The driver calls `bind_auditor` once the
        summary is in hand so the periodic JSONL flush has
        access to the live drop list.
        """

        self._auditor = auditor

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
        immediately so a tee pipe does not buffer the output."""

        if self._stream is None:
            return
        self._stream.write(line)
        self._stream.flush()


# Note: `_TARGET_TICK_COUNT` and `_AUDIT_FLUSH_INTERVAL_SECONDS`
# above are deliberately tunable module-private constants. The
# tests reach in to assert stride behaviour; future operators
# who want a chattier or quieter cadence can adjust them in one
# place. Keep them private (`_`-prefixed) so external callers
# do not depend on the exact values.
