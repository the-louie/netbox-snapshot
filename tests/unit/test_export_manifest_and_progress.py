"""FEAT-15/16 manifest, PerfTimer, and progress-log tests."""

from __future__ import annotations

import time
from pathlib import Path

from nbsnap.export.manifest import PerfTimer
from nbsnap.export.progress import ProgressLog, resume_from
from nbsnap.snapshot import Manifest


def test_manifest_roundtrip(tmp_path: Path) -> None:
    m = Manifest(source_url_hash="deadbeef0001", netbox_version="4.6.2", counts={"a": 1})
    m.write(tmp_path / "manifest.json")
    loaded = Manifest.load(tmp_path / "manifest.json")
    assert loaded.source_url_hash == "deadbeef0001"
    assert loaded.counts == {"a": 1}


def test_perf_timer_accumulates() -> None:
    sink: dict[str, float] = {}
    timer = PerfTimer(sink)
    with timer.timer("step"):
        time.sleep(0.001)
    with timer.timer("step"):
        time.sleep(0.001)
    assert sink["step"] > 0
    assert "step" in sink


def test_progress_log_resume_picks_up_done_entries(tmp_path: Path) -> None:
    log = ProgressLog(tmp_path / "progress.jsonl")
    log.append("dcim.site", "all", "done")
    log.append("dcim.device", "all", "pending")
    completed = resume_from(tmp_path / "progress.jsonl")
    assert completed == {"dcim.site"}


def test_progress_log_empty_returns_empty_set(tmp_path: Path) -> None:
    assert resume_from(tmp_path / "missing.jsonl") == set()
