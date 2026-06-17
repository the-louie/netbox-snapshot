"""ARCH-02f: ``phase2_runner`` import path resolves.

ARCH-02f lifts the public reference for Phase 2 to a stable module
path; we do not duplicate the behaviour tests that already live
under :mod:`tests.unit.test_import_phase2`. The point of this file
is to lock the import contract so a caller that imports from the
new path keeps working through later refactors.
"""

from __future__ import annotations


def test_run_phase2_re_exported_from_phase2_runner() -> None:
    from nbsnap.import_.phase2 import run_phase2 as legacy
    from nbsnap.import_.phase2_runner import run_phase2 as new

    assert legacy is new


def test_phase2_runner_all_exports_match() -> None:
    import nbsnap.import_.phase2_runner as mod

    assert set(mod.__all__) == {"Phase2Outcome", "Phase2Summary", "run_phase2"}
