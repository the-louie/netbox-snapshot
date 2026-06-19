"""TEST-09b scale-test runner.

Asserts that an export of the destination stack completes inside
the documented wall-clock target. The assertion is a generous
upper bound (600s) so the minimal CI seed passes trivially and a
future scale fixture (~5,000 objects per TEST-09b) still has
budget. The previous skipif gate (`NBSNAP_RUN_SCALE=1`) was
removed because a skip per CI run runs counter to the project's
"no skipped CI steps" goal; if the future scale fixture pushes
the export past 10 minutes, the assertion fires loudly instead
of hiding behind the gate.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nbsnap.export.driver import run_export
from nbsnap.http.client import NetboxHTTP

from .conftest import DEST_TOKEN, DEST_URL


@pytest.mark.usefixtures("require_stack")
def test_scale_export_under_ten_minutes(tmp_path: Path) -> None:
    """A full export of the destination stack must finish inside
    the 600 second budget. The seed today is small so the test
    runs in seconds; the budget gives room for the scale fixture
    landed by TEST-09 later.
    """

    http = NetboxHTTP(DEST_URL, DEST_TOKEN, verify_tls=False)
    start = time.perf_counter()
    run_export(http, tmp_path / "scale-snap")
    elapsed = time.perf_counter() - start
    assert elapsed < 600, f"scale export took {elapsed:.1f}s (>600s budget)"
