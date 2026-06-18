"""Phase-1 driver for the import side (ARCH-02e scaffold).

Phase 1 walks the snapshot directory in plan order, resolves every
foreign key in each record's body to a destination id, and POSTs
the record. The work today lives inside
:func:`nbsnap.import_.driver.run_import`; this module is the
extraction target. ARCH-02e ships the file plus a thin
:func:`run_phase1` wrapper so the unit tests of ARCH-02i have a
single import point to drive. ARCH-02h is the follow-up that moves
the body of the loop out of ``driver.py`` and into this module.

Why a scaffold now
------------------
Splitting the 200-plus-line Phase-1 loop out of ``driver.py`` is a
high-risk surgical move: it touches the hottest path the import
runs and any subtle behaviour change would surface as a
silent-data-loss incident. The scaffold lets us:

* import :func:`run_phase1` from the audit-documented location;
* lock the contract (signature, return type) with unit tests;
* land subsequent ARCH-02h chunks as small, reviewable diffs that
  do not re-touch the public API.

Until ARCH-02h moves the body, :func:`run_phase1` re-exports the
work done in ``driver.run_import``'s closure as a no-op call into
the legacy path. The two contracts converge once ARCH-02h is in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nbsnap.import_.resolve_context import ResolveContext


def run_phase1(plan_order: list[str], ctx: ResolveContext) -> None:
    """Walk the plan and POST each row through the FK resolver.

    The audit-documented contract:

    * Iterate ``plan_order`` (the manifest's content-type order).
    * For each content type, stream the snapshot JSONL.
    * For each row, call :func:`_resolve_body_via_ctx` then
      :func:`upsert`.
    * Record drop events into ``ctx.auditor``.

    Until ARCH-02h moves the body, this scaffold raises
    :class:`NotImplementedError` so a caller cannot mistake the
    scaffold for the working implementation. ``driver.run_import``
    keeps its in-line copy of the loop as the live code path.
    """

    raise NotImplementedError(
        "ARCH-02e scaffold: the working Phase-1 loop still lives in "
        "nbsnap.import_.driver.run_import; ARCH-02h moves the body here."
    )


__all__ = ["run_phase1"]
