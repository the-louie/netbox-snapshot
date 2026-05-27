"""FEAT-36h tests for the enum-dict preflight scan."""

from __future__ import annotations

import json
from pathlib import Path

from nbsnap.import_.preflight import (
    PreflightReport,
    sample_enum_dict_check,
)
from nbsnap.schema.status import VersionSkew


def _write_jsonl(path: Path, *rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_enum_dict_in_first_row_is_flagged(tmp_path: Path) -> None:
    """A snapshot row where `status` is the legacy
    `{value, label}` dict is flagged with the file path and
    field name."""

    _write_jsonl(tmp_path / "dcim" / "sites.jsonl", {
        "natural_key": ["hall-a"],
        "body": {
            "name": "Hall-A", "slug": "a",
            "status": {"value": "active", "label": "Active"},
        },
    })
    issues = sample_enum_dict_check(tmp_path)
    assert len(issues) == 1
    assert "dcim/sites.jsonl" in issues[0]
    assert "status" in issues[0]


def test_clean_snapshot_returns_no_issues(tmp_path: Path) -> None:
    """A snapshot where every choice field is a bare value
    passes the check with an empty issue list."""

    _write_jsonl(tmp_path / "dcim" / "sites.jsonl", {
        "natural_key": ["hall-a"],
        "body": {"name": "Hall-A", "slug": "a", "status": "active"},
    })
    assert sample_enum_dict_check(tmp_path) == []


def test_audit_files_are_skipped(tmp_path: Path) -> None:
    """`flags.jsonl`, `progress.jsonl`, `_deferred.jsonl`,
    `audit.jsonl` carry different shapes and must not be sampled."""

    for name in ("flags.jsonl", "progress.jsonl",
                 "_deferred.jsonl", "audit.jsonl"):
        # Write a row that WOULD trigger the check if not skipped.
        _write_jsonl(tmp_path / name, {
            "natural_key": ["x"],
            "body": {"status": {"value": "active", "label": "Active"}},
        })
    assert sample_enum_dict_check(tmp_path) == []


def test_one_issue_per_file_even_with_multiple_offending_fields(
    tmp_path: Path,
) -> None:
    """A row with two enum-dict fields surfaces ONE issue, the
    operator only needs to know the file is bad."""

    _write_jsonl(tmp_path / "dcim" / "devices.jsonl", {
        "natural_key": [["a"], "d"],
        "body": {
            "name": "d",
            "status": {"value": "active", "label": "Active"},
            "airflow": {"value": "front-to-rear", "label": "Front to rear"},
        },
    })
    issues = sample_enum_dict_check(tmp_path)
    assert len(issues) == 1


def test_empty_body_is_safe(tmp_path: Path) -> None:
    """An empty body dict (or a missing one) does not crash and
    does not produce a false positive."""

    _write_jsonl(tmp_path / "dcim" / "sites.jsonl", {
        "natural_key": ["x"], "body": {},
    })
    assert sample_enum_dict_check(tmp_path) == []


def test_malformed_jsonl_first_row_is_skipped(tmp_path: Path) -> None:
    """A JSON-broken first line does not abort the scan; the
    file is skipped and the rest of the snapshot is still
    inspected."""

    bad = tmp_path / "dcim" / "broken.jsonl"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not valid json\n", encoding="utf-8")
    # And a clean file alongside.
    _write_jsonl(tmp_path / "dcim" / "sites.jsonl", {
        "natural_key": ["x"],
        "body": {"name": "x", "status": "active"},
    })
    assert sample_enum_dict_check(tmp_path) == []


def test_check_reports_multiple_files(tmp_path: Path) -> None:
    """Two distinct offending files surface as two issues."""

    _write_jsonl(tmp_path / "dcim" / "sites.jsonl", {
        "natural_key": ["a"],
        "body": {"status": {"value": "active", "label": "Active"}},
    })
    _write_jsonl(tmp_path / "dcim" / "devices.jsonl", {
        "natural_key": [["a"], "d"],
        "body": {"status": {"value": "active", "label": "Active"}},
    })
    issues = sample_enum_dict_check(tmp_path)
    assert len(issues) == 2


# ---------------------------------------------------------------------------
# PreflightReport.is_blocking
# ---------------------------------------------------------------------------


def test_is_blocking_when_enum_dict_issues_present() -> None:
    """The default behaviour is to refuse a snapshot that
    carries the legacy enum-dict shape."""

    r = PreflightReport()
    r.snapshot_format_issues = ["dcim/sites.jsonl: field 'status' ..."]
    assert r.is_blocking(VersionSkew.MAJOR) is True


def test_bypass_clears_enum_dict_block() -> None:
    """`allow_enum_dict_bypass=True` lets the legacy snapshot
    through; the import-side coerce should still rescue most
    rows."""

    r = PreflightReport()
    r.snapshot_format_issues = ["dcim/sites.jsonl: field 'status' ..."]
    assert r.is_blocking(VersionSkew.MAJOR, allow_enum_dict_bypass=True) is False


def test_missing_content_types_still_blocks_even_with_bypass() -> None:
    """The bypass is scoped to enum-dict; missing CTs still
    abort because the destination cannot accept the data."""

    r = PreflightReport()
    r.missing_content_types = {"dcim.fakefake"}
    assert r.is_blocking(VersionSkew.MAJOR, allow_enum_dict_bypass=True) is True
