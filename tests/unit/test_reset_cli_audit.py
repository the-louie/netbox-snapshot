"""FEAT-37e audit-JSONL tests.

Two things this file pins:

1. `_delete_ids_with_audit` produces the documented per-id
   audit lines for happy, fallback, and failed outcomes.
2. `_flush_audit` writes the JSONL file at the --audit-out
   path (creating parent dirs as needed) and is a no-op when
   --audit-out is None.

The end-to-end check that `run_reset_cli` actually persists
the audit file is in the integration test
`tests/integration/test_reset_destination.py`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nbsnap.http.client import NetboxHTTPError
from nbsnap.reset_cli import (
    _delete_ids_with_audit,
    _flush_audit,
    run_reset_cli,
)


def _args(**override) -> argparse.Namespace:
    defaults = {
        "url": "https://dest.example/",
        "token": "tok",
        "no_verify_tls": False,
        "content_types": "dcim.site",
        "keep": [],
        "apply": True,
        "confirmed": True,
        "on_error": "continue",
        "audit_out": None,
    }
    defaults.update(override)
    return argparse.Namespace(**defaults)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "NB_SOURCE_URL",
        "NB_SOURCE_TOKEN",
        "NB_DESTINATION_URL",
        "NB_DESTINATION_TOKEN",
        "NB_URL",
        "NB_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# _delete_ids_with_audit
# ---------------------------------------------------------------------------


def test_audit_records_deleted_outcome_on_bulk_success() -> None:
    """Happy path: every id in the batch lands as `deleted`."""

    http = MagicMock()
    http._request.return_value = None  # 204 No Content
    failures, audit = _delete_ids_with_audit(
        http, "dcim/sites/", [1, 2, 3], "dcim.site"
    )
    assert failures == []
    assert len(audit) == 3
    parsed = [json.loads(line) for line in audit]
    assert {row["id"] for row in parsed} == {1, 2, 3}
    assert all(row["outcome"] == "deleted" for row in parsed)
    assert all(row["content_type"] == "dcim.site" for row in parsed)


def test_audit_records_deleted_fallback_on_4xx_bulk() -> None:
    """4xx bulk failure, per-id fallback succeeds; outcome
    is `deleted-fallback` to distinguish from clean bulk."""

    http = MagicMock()
    http._request.side_effect = [
        NetboxHTTPError("DELETE", "dcim/sites/", 409, "bulk-conflict"),
        None,  # per-id DELETE for id=1
        None,  # per-id DELETE for id=2
    ]
    failures, audit = _delete_ids_with_audit(
        http, "dcim/sites/", [1, 2], "dcim.site"
    )
    assert failures == []
    parsed = [json.loads(line) for line in audit]
    assert {row["outcome"] for row in parsed} == {"deleted-fallback"}
    assert {row["id"] for row in parsed} == {1, 2}


def test_audit_records_failed_with_truncated_message() -> None:
    """A per-id 409 records `failed` and truncates the message
    so a huge NetBox error body does not blow up the audit."""

    long_body = "x" * 500
    http = MagicMock()
    http._request.side_effect = [
        NetboxHTTPError("DELETE", "dcim/sites/", 409, "bulk-conflict"),
        NetboxHTTPError("DELETE", "dcim/sites/1/", 409, long_body),
    ]
    failures, audit = _delete_ids_with_audit(
        http, "dcim/sites/", [1], "dcim.site"
    )
    assert len(failures) == 1
    parsed = [json.loads(line) for line in audit]
    assert parsed[0]["outcome"] == "failed"
    # 200-char cap per the helper's docstring.
    assert len(parsed[0]["message"]) <= 220


def test_audit_records_5xx_failure_without_per_id_retries() -> None:
    """5xx surfaces the whole batch as `failed`, no fallback."""

    http = MagicMock()
    http._request.side_effect = [
        NetboxHTTPError("DELETE", "dcim/sites/", 503, "service unavailable"),
    ]
    failures, audit = _delete_ids_with_audit(
        http, "dcim/sites/", [1, 2], "dcim.site"
    )
    assert len(failures) == 2
    parsed = [json.loads(line) for line in audit]
    assert {row["outcome"] for row in parsed} == {"failed"}
    # Only one HTTP call fired (the bulk attempt).
    assert http._request.call_count == 1


# ---------------------------------------------------------------------------
# _flush_audit
# ---------------------------------------------------------------------------


def test_flush_audit_writes_jsonl(tmp_path: Path) -> None:
    """`--audit-out` writes one JSON object per line."""

    lines = [
        '{"id": 1, "outcome": "deleted"}',
        '{"id": 2, "outcome": "deleted"}',
    ]
    target = tmp_path / "audit.jsonl"
    _flush_audit(target, lines)
    content = target.read_text(encoding="utf-8").splitlines()
    assert content == [
        '{"id": 1, "outcome": "deleted"}',
        '{"id": 2, "outcome": "deleted"}',
    ]


def test_flush_audit_creates_parent_dir(tmp_path: Path) -> None:
    """An `--audit-out` pointing into a non-existent dir works."""

    target = tmp_path / "nested" / "deep" / "audit.jsonl"
    _flush_audit(target, ['{"x": 1}'])
    assert target.exists()


def test_flush_audit_is_noop_when_path_is_none(tmp_path: Path) -> None:
    """No --audit-out, no file written, no exceptions raised."""

    _flush_audit(None, ['{"x": 1}'])
    # tmp_path stays empty
    assert list(tmp_path.iterdir()) == []


def test_flush_audit_writes_empty_file_when_no_lines(tmp_path: Path) -> None:
    """An empty audit list still produces a file (an empty one)
    so the operator can rely on the file's existence as a
    signal that the command ran."""

    target = tmp_path / "audit.jsonl"
    _flush_audit(target, [])
    assert target.exists()
    assert target.read_text() == ""


# ---------------------------------------------------------------------------
# Integration through run_reset_cli
# ---------------------------------------------------------------------------


def test_run_reset_cli_persists_audit_when_path_given(
    tmp_path: Path,
) -> None:
    """End-to-end: `run_reset_cli` with `--audit-out` writes the
    JSONL file after the apply run."""

    audit_path = tmp_path / "audit.jsonl"
    http = MagicMock()
    http.is_source.return_value = False
    http.base_url = "https://dest.example/"
    http.get_all.side_effect = lambda _ep: iter(
        [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    )
    http._request.return_value = None

    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=http):
        run_reset_cli(_args(audit_out=audit_path))

    rows = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["id"] for row in rows} == {1, 2}
    assert {row["outcome"] for row in rows} == {"deleted"}


def test_run_reset_cli_persists_audit_on_stop_path(
    tmp_path: Path,
) -> None:
    """When --on-error stop aborts, the audit captured so far
    must be written so the operator can see what happened."""

    audit_path = tmp_path / "audit.jsonl"
    http = MagicMock()
    http.is_source.return_value = False
    http.base_url = "https://dest.example/"
    http.get_all.side_effect = lambda _ep: iter([{"id": 9, "name": "broken"}])
    # Bulk fails, per-id also fails: row 9 goes into failures.
    http._request.side_effect = [
        NetboxHTTPError("DELETE", "dcim/sites/", 409, "bulk-conflict"),
        NetboxHTTPError("DELETE", "dcim/sites/9/", 409, "single-conflict"),
    ]

    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=http):
        run_reset_cli(_args(on_error="stop", audit_out=audit_path))

    rows = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["outcome"] == "failed"
    assert rows[0]["id"] == 9
