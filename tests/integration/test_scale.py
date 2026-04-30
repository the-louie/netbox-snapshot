"""TEST-09b scale-test runner.

Skipped by default because the scale fixtures take ~10 minutes to
seed into a fresh NetBox. Enable by setting `NBSNAP_RUN_SCALE=1`
in the test environment.

Asserts that an export of the scaled stack completes inside the
documented wall-clock target.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from nbsnap.export.driver import run_export
from nbsnap.http.client import NetboxHTTP

from .conftest import DEST_TOKEN, DEST_URL


@pytest.mark.skipif(
    os.environ.get("NBSNAP_RUN_SCALE") != "1",
    reason="scale tests are heavy, enable with NBSNAP_RUN_SCALE=1",
)
@pytest.mark.usefixtures("require_stack")
def test_scale_export_under_ten_minutes(tmp_path: Path) -> None:
    """5,000 objects from the scale fixture export inside 10 minutes."""

    http = NetboxHTTP(DEST_URL, DEST_TOKEN, verify_tls=False)
    start = time.perf_counter()
    run_export(http, tmp_path / "scale-snap")
    elapsed = time.perf_counter() - start
    assert elapsed < 600, f"scale export took {elapsed:.1f}s (>600s budget)"
