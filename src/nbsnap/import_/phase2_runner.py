"""Phase-2 driver for the import side (ARCH-02f).

Phase 2 walks the deferred-edge queue produced by the look-ahead
resolver in Phase 1 and PATCHes each cycle-closing FK against the
destination. The work already lives in
:func:`nbsnap.import_.phase2.run_phase2`; ARCH-02f gives the
operation an audit-documented import path
(``nbsnap.import_.phase2_runner``) so external callers and the
ARCH-02h driver slim-down can target the same location.

Until ARCH-02h moves more orchestration logic in, this module is a
thin re-export of the existing :func:`run_phase2`. The motivation
is the same as ARCH-02e (phase1_runner): lock the public surface
first, land internal changes later as small diffs.
"""

from __future__ import annotations

from nbsnap.import_.phase2 import Phase2Outcome, Phase2Summary, run_phase2

__all__ = ["Phase2Outcome", "Phase2Summary", "run_phase2"]
