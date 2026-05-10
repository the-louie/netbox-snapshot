"""Tests for the import CLI's hardened error handling.

Each failure mode the import CLI claims to handle gets its own
focused test below. The expectations are pinned to the documented
exit codes so a future refactor cannot quietly change the
operator-visible contract.

Pre-flight validation is offline (no sockets), so those tests use
real filesystem fixtures. Network-shaped failures stub out
`run_import` to inject the exception we want.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from nbsnap.http.client import NetboxHTTPError
from nbsnap.import_cli import (
    EXIT_BAD_INVOCATION,
    EXIT_DESTINATION_UNREACHABLE,
    EXIT_OK,
    EXIT_PREFLIGHT_BLOCKED,
    EXIT_ROW_FAILURES,
    EXIT_UNEXPECTED,
    run_import_cli,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _write_minimal_snapshot(root: Path) -> Path:
    """Build a snapshot directory with a tiny but valid manifest + schema.

    Returns the snapshot dir. The schema is intentionally minimal,
    enough to satisfy _validate_invocation; downstream behaviour is
    stubbed in the tests that need it.
    """
    snap = root / "snapshot"
    snap.mkdir()
    (snap / "schema").mkdir()
    (snap / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source_url": "https://src.example/",
                "netbox_version": "4.6.2",
                "counts": {"dcim.site": 1},
                "perf": {},
                "deferred_edges": [],
            }
        ),
        encoding="utf-8",
    )
    (snap / "schema" / "openapi.json").write_text(
        json.dumps({"paths": {}, "components": {"schemas": {}}}),
        encoding="utf-8",
    )
    return snap


def _args(snap: Path, **overrides) -> argparse.Namespace:
    """Build an `argparse.Namespace` matching what the CLI parser produces."""
    defaults = {
        "url": "https://dest.example/",
        "token": "dest-token",
        "no_verify_tls": False,
        "in_dir": snap,
        "max_version_skew": "minor",
        "on_error": "continue",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe NB_* env vars so tests are deterministic."""
    for k in (
        "NB_DESTINATION_URL",
        "NB_DESTINATION_TOKEN",
        "NB_SOURCE_URL",
        "NB_SOURCE_TOKEN",
        "NB_URL",
        "NB_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# Pre-flight validation, all offline
# ---------------------------------------------------------------------------


def test_returns_3_when_snapshot_dir_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(tmp_path / "does-not-exist")
    rc = run_import_cli(args)
    assert rc == EXIT_BAD_INVOCATION
    assert "snapshot directory not found" in capsys.readouterr().err


def test_returns_3_when_snapshot_path_is_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bogus = tmp_path / "not-a-dir.txt"
    bogus.write_text("x")
    rc = run_import_cli(_args(bogus))
    assert rc == EXIT_BAD_INVOCATION
    assert "must be a directory" in capsys.readouterr().err


def test_returns_3_when_manifest_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snap = tmp_path / "snapshot"
    snap.mkdir()
    # No manifest.
    rc = run_import_cli(_args(snap))
    assert rc == EXIT_BAD_INVOCATION
    assert "manifest missing" in capsys.readouterr().err


def test_returns_3_when_manifest_is_invalid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snap = tmp_path / "snapshot"
    snap.mkdir()
    (snap / "manifest.json").write_text("{not json")
    rc = run_import_cli(_args(snap))
    assert rc == EXIT_BAD_INVOCATION
    assert "not valid JSON" in capsys.readouterr().err


def test_returns_3_when_manifest_lacks_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snap = tmp_path / "snapshot"
    snap.mkdir()
    (snap / "manifest.json").write_text(json.dumps({"version": 1}))
    rc = run_import_cli(_args(snap))
    assert rc == EXIT_BAD_INVOCATION
    assert "missing the required `counts` field" in capsys.readouterr().err


def test_returns_3_when_schema_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snap = tmp_path / "snapshot"
    snap.mkdir()
    (snap / "manifest.json").write_text(json.dumps({"counts": {}}))
    # No schema/openapi.json.
    rc = run_import_cli(_args(snap))
    assert rc == EXIT_BAD_INVOCATION
    assert "schema missing" in capsys.readouterr().err


def test_returns_3_when_no_url_configured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snap = _write_minimal_snapshot(tmp_path)
    rc = run_import_cli(_args(snap, url=None, token=None))
    assert rc == EXIT_BAD_INVOCATION
    err = capsys.readouterr().err
    assert "no URL configured" in err or "no token configured" in err


# ---------------------------------------------------------------------------
# Network-shaped failures, stubbing run_import
# ---------------------------------------------------------------------------


def test_returns_4_on_tls_verification_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The "production banner" warning, but for the destination."""

    snap = _write_minimal_snapshot(tmp_path)
    with patch(
        "nbsnap.import_cli.run_import",
        side_effect=requests.exceptions.SSLError("cert verify failed"),
    ):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_DESTINATION_UNREACHABLE
    err = capsys.readouterr().err
    assert "TLS verification failed" in err
    assert "--no-verify-tls" in err


def test_returns_4_on_connection_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snap = _write_minimal_snapshot(tmp_path)
    with patch(
        "nbsnap.import_cli.run_import",
        side_effect=requests.exceptions.ConnectionError("name resolution"),
    ):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_DESTINATION_UNREACHABLE
    assert "cannot reach destination" in capsys.readouterr().err


def test_returns_4_on_401_unauthorized(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snap = _write_minimal_snapshot(tmp_path)
    with patch(
        "nbsnap.import_cli.run_import",
        side_effect=NetboxHTTPError(
            "GET", "https://dest.example/api/status/", 401, '{"detail":"Invalid token"}'
        ),
    ):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_DESTINATION_UNREACHABLE
    assert "authentication failed" in capsys.readouterr().err


def test_returns_4_on_403_forbidden(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snap = _write_minimal_snapshot(tmp_path)
    with patch(
        "nbsnap.import_cli.run_import",
        side_effect=NetboxHTTPError(
            "GET", "https://dest.example/api/status/", 403, '{"detail":"No permission"}'
        ),
    ):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_DESTINATION_UNREACHABLE
    assert "authentication failed" in capsys.readouterr().err


def test_returns_4_on_500_destination_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """5xx from destination surfaces with the body snippet."""

    snap = _write_minimal_snapshot(tmp_path)
    with patch(
        "nbsnap.import_cli.run_import",
        side_effect=NetboxHTTPError(
            "GET", "https://dest.example/api/status/", 500, "Internal server error"
        ),
    ):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_DESTINATION_UNREACHABLE
    err = capsys.readouterr().err
    assert "HTTP 500" in err
    assert "Internal server error" in err


def test_returns_5_on_unexpected_exception(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Last-resort handler keeps the operator from seeing a raw traceback."""

    snap = _write_minimal_snapshot(tmp_path)
    with patch(
        "nbsnap.import_cli.run_import",
        side_effect=RuntimeError("something deeply unexpected"),
    ):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_UNEXPECTED
    err = capsys.readouterr().err
    assert "unexpected error" in err
    assert "something deeply unexpected" in err


# ---------------------------------------------------------------------------
# Pre-flight + success path, stubbing run_import to return summaries
# ---------------------------------------------------------------------------


def _summary_with(**kwargs):
    """Build a fake ImportSummary the CLI can render."""
    from collections import Counter

    from nbsnap.import_.driver import ImportSummary
    from nbsnap.import_.preflight import PreflightReport
    from nbsnap.import_.upsert import UpsertOutcome

    pre = PreflightReport()
    pre.missing_content_types = kwargs.pop("missing_content_types", set())
    pre.version_skew = kwargs.pop("version_skew", pre.version_skew)
    summary = ImportSummary(preflight=pre)
    summary.counts = Counter({UpsertOutcome.CREATED: kwargs.pop("created", 0)})
    summary.failures = kwargs.pop("failures", [])
    return summary


def test_returns_1_when_preflight_blocks_on_missing_content_types(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snap = _write_minimal_snapshot(tmp_path)
    with patch(
        "nbsnap.import_cli.run_import",
        return_value=_summary_with(missing_content_types={"plugin.foo"}),
    ):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_PREFLIGHT_BLOCKED
    err = capsys.readouterr().err
    assert "missing content types" in err
    assert "plugin.foo" in err


def test_returns_1_when_preflight_blocks_on_version_skew(tmp_path: Path) -> None:
    from nbsnap.schema.status import VersionSkew

    snap = _write_minimal_snapshot(tmp_path)
    with patch(
        "nbsnap.import_cli.run_import",
        return_value=_summary_with(version_skew=VersionSkew.MAJOR),
    ):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_PREFLIGHT_BLOCKED


def test_returns_2_when_any_row_fails(tmp_path: Path) -> None:
    from nbsnap.import_.upsert import UpsertOutcome, UpsertResult

    snap = _write_minimal_snapshot(tmp_path)
    failure = UpsertResult(
        outcome=UpsertOutcome.FAILED,
        content_type="dcim.site",
        natural_key=("hall-d",),
        destination_id=None,
        message="POST failed: 400",
    )
    with patch(
        "nbsnap.import_cli.run_import",
        return_value=_summary_with(failures=[failure]),
    ):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_ROW_FAILURES


def test_returns_0_on_clean_success(tmp_path: Path) -> None:
    snap = _write_minimal_snapshot(tmp_path)
    with patch("nbsnap.import_cli.run_import", return_value=_summary_with(created=42)):
        rc = run_import_cli(_args(snap))
    assert rc == EXIT_OK


def test_no_verify_tls_flag_disables_verify(tmp_path: Path) -> None:
    """`--no-verify-tls` flows into the constructed NetboxHTTP."""

    snap = _write_minimal_snapshot(tmp_path)
    captured: dict = {}

    def fake_from_env(_role: str, **kwargs):
        captured.update(kwargs)
        return _FakeHttp()

    with (
        patch("nbsnap.import_cli.NetboxHTTP.from_env", side_effect=fake_from_env),
        patch("nbsnap.import_cli.run_import", return_value=_summary_with()),
    ):
        run_import_cli(_args(snap, no_verify_tls=True))
    assert captured["verify_tls"] is False


class _FakeHttp:
    base_url = "https://dest.example/"
