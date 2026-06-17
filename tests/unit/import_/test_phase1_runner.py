"""ARCH-02e: ``phase1_runner`` scaffold contract tests.

Until ARCH-02h moves the loop body out of ``driver.run_import``,
:func:`run_phase1` raises :class:`NotImplementedError` to make sure
no caller mistakenly wires up the scaffold instead of the live path.
The test pins:

* the module exists at the audit-documented path;
* the scaffold raises ``NotImplementedError`` with a message that
  names the migration ticket so a future contributor knows where to
  finish the work.
"""

from __future__ import annotations

import pytest


def test_phase1_runner_module_imports() -> None:
    """The audit-documented import path resolves."""

    import nbsnap.import_.phase1_runner as mod  # noqa: F401

    assert hasattr(mod, "run_phase1")


def test_run_phase1_scaffold_refuses_to_run() -> None:
    """The scaffold is not a live implementation, calling it raises."""

    from nbsnap.import_.phase1_runner import run_phase1

    with pytest.raises(NotImplementedError) as exc:
        run_phase1([], None)  # type: ignore[arg-type]
    assert "ARCH-02h" in str(exc.value)
