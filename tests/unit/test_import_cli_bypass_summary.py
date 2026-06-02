"""FEAT-47: when `--allow-enum-dict-bypass` is set, the
preflight per-file list is suppressed in stderr and routed to
`preflight-bypass.jsonl` next to the audit log.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nbsnap.import_cli import run_import_cli
from nbsnap.schema.status import VersionSkew


def _args(snap: Path, **overrides) -> argparse.Namespace:
    defaults = {
        "url": "https://dest.example/",
        "token": "dest-token",
        "no_verify_tls": False,
        "in_dir": snap,
        "max_version_skew": "minor",
        "on_error": "continue",
        "audit_out": None,
        "allow_enum_dict_bypass": True,
        "max_parse_errors": 0,
        "audit_summary_limit": 10,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("NB_DESTINATION_URL", "NB_DESTINATION_TOKEN", "NB_URL", "NB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def _build_snapshot(tmp_path: Path) -> Path:
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1,
        "netbox_version": "4.6.2",
        "counts": {},
        "deferred_edges": [],
    }))
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    (schema_dir / "openapi.json").write_text(json.dumps({
        "openapi": "3.0.3", "paths": {}, "components": {"schemas": {}}
    }))
    return tmp_path


def _fake_preflight_with_issues() -> MagicMock:
    pre = MagicMock()
    pre.is_blocking.return_value = False
    pre.version_skew = VersionSkew.NONE
    pre.missing_content_types = set()
    pre.snapshot_format_issues = [
        {"path": f"dcim/devices-{i}.jsonl",
         "field": "status",
         "rows_affected": 1}
        for i in range(12)
    ]
    return pre


def test_bypass_active_suppresses_verbose_list(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    snap = _build_snapshot(tmp_path)

    fake_pre = _fake_preflight_with_issues()
    fake_summary = MagicMock()
    fake_summary.preflight = fake_pre
    fake_summary.counts = {}
    fake_summary.auditor.render_summary.return_value = ""
    fake_summary.auditor.events = []
    fake_summary.auditor.write_jsonl = MagicMock()
    fake_summary.phase2 = None
    fake_summary.failures = []
    fake_summary.parse_errors = []

    with patch("nbsnap.import_cli.run_import", return_value=fake_summary), \
         patch("nbsnap.import_cli.NetboxHTTP"):
        run_import_cli(_args(snap, allow_enum_dict_bypass=True))

    err = capsys.readouterr().err
    assert "enum-dict bypass active: 12 files" in err
    # Verbose block must be gone.
    assert "snapshot format issues detected" not in err
    # Forensic file lands next to audit.
    bypass_path = snap / "preflight-bypass.jsonl"
    assert bypass_path.exists()
    rows = [json.loads(line) for line in bypass_path.read_text().splitlines()]
    assert len(rows) == 12


def test_bypass_inactive_still_emits_verbose_block(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    snap = _build_snapshot(tmp_path)

    fake_pre = _fake_preflight_with_issues()
    fake_summary = MagicMock()
    fake_summary.preflight = fake_pre
    fake_summary.counts = {}
    fake_summary.auditor.render_summary.return_value = ""
    fake_summary.auditor.events = []
    fake_summary.auditor.write_jsonl = MagicMock()
    fake_summary.phase2 = None
    fake_summary.failures = []
    fake_summary.parse_errors = []

    with patch("nbsnap.import_cli.run_import", return_value=fake_summary), \
         patch("nbsnap.import_cli.NetboxHTTP"):
        run_import_cli(_args(snap, allow_enum_dict_bypass=False))

    err = capsys.readouterr().err
    assert "snapshot format issues detected" in err
    assert "enum-dict bypass active" not in err
