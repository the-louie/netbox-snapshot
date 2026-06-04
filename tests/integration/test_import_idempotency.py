"""TEST-06: a second `nbsnap import` of the same snapshot against
the same destination must leave every record at NOOP.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import (
    DEST_TOKEN, DEST_URL, SOURCE_TOKEN, SOURCE_URL,
)


@pytest.mark.usefixtures("require_stack")
def test_second_run_is_all_noop(tmp_path: Path) -> None:
    snap = tmp_path / "snap"
    subprocess.run(
        [
            sys.executable, "-m", "nbsnap", "export",
            "--url", SOURCE_URL,
            "--token", SOURCE_TOKEN,
            "--out", str(snap),
        ],
        check=True,
    )

    # First import: expect mostly CREATED.
    first = subprocess.run(
        [
            sys.executable, "-m", "nbsnap", "import",
            "--url", DEST_URL,
            "--token", DEST_TOKEN,
            "--in", str(snap),
            "--on-error", "continue",
        ],
        capture_output=True, text=True, check=False,
    )
    assert "created:" in first.stderr

    # Second import: expect every result NOOP.
    second = subprocess.run(
        [
            sys.executable, "-m", "nbsnap", "import",
            "--url", DEST_URL,
            "--token", DEST_TOKEN,
            "--in", str(snap),
            "--on-error", "continue",
        ],
        capture_output=True, text=True, check=False,
    )
    assert "created: 0" in second.stderr, second.stderr
    assert "updated: 0" in second.stderr, second.stderr
    # NOOP count > 0 is the proof that the second run actually
    # walked records and decided no diff existed.
    assert "noop:" in second.stderr
