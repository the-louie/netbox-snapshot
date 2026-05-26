"""FEAT-37b safety-gate tests.

Three gates, three exit codes, one test per scenario plus a
happy-path that all three pass.

The pattern. Each test stubs `NetboxHTTP.from_env` so no real
network or env var is required. Stubbed clients return a
controlled `is_source()` value. The test then asserts on the
return code and the stderr message text.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from nbsnap.reset_cli import (
    EXIT_BLOCKED_BY_SOURCE_GUARD,
    EXIT_NEEDS_APPLY_FLAGS,
    EXIT_OK,
    run_reset_cli,
)


def _args(**override) -> argparse.Namespace:
    """Build a fully-populated Namespace; tests override only what they exercise."""

    defaults = {
        "url": "https://dest.example/",
        "token": "tok",
        "no_verify_tls": False,
        "content_types": None,
        "keep": [],
        "apply": False,
        "confirmed": False,
        "on_error": "stop",
        "audit_out": None,
    }
    defaults.update(override)
    return argparse.Namespace(**defaults)


def _fake_client(is_source: bool = False, base_url: str = "https://dest.example/") -> MagicMock:
    """Build a minimal `NetboxHTTP` stand-in. Tests only touch
    `is_source()` and `base_url`; nothing else is needed at this
    skeleton + safety stage."""

    client = MagicMock()
    client.is_source.return_value = is_source
    client.base_url = base_url
    return client


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty NB_* env vars so `from_env` does not pick up the dev
    operator's real credentials during unit tests."""

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
# Gate 1: source-URL guard
# ---------------------------------------------------------------------------


def test_returns_4_when_destination_matches_source_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If --url points at the same host:port as NB_SOURCE_URL,
    the command refuses before any GET fires."""

    fake = _fake_client(is_source=True, base_url="https://prod.example/")
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=fake):
        rc = run_reset_cli(_args(url="https://prod.example/"))

    assert rc == EXIT_BLOCKED_BY_SOURCE_GUARD
    err = capsys.readouterr().err
    assert "matches NB_SOURCE_URL" in err
    assert "CLAUDE.md" in err  # the message points at the canonical policy


def test_source_guard_fires_even_when_apply_flags_present(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Apply flags do not override the source guard. The order
    of gates matters: source guard wins."""

    fake = _fake_client(is_source=True)
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=fake):
        rc = run_reset_cli(_args(apply=True, confirmed=True))

    assert rc == EXIT_BLOCKED_BY_SOURCE_GUARD
    assert "matches NB_SOURCE_URL" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Gate 2: --apply needs --i-know-what-im-doing
# ---------------------------------------------------------------------------


def test_returns_1_when_apply_without_confirmation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--apply alone is not enough."""

    fake = _fake_client()
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=fake):
        rc = run_reset_cli(_args(apply=True, confirmed=False))

    assert rc == EXIT_NEEDS_APPLY_FLAGS
    err = capsys.readouterr().err
    assert "--i-know-what-im-doing" in err


def test_confirmed_without_apply_is_treated_as_dry_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The symmetric case: --i-know-what-im-doing without --apply
    is just dry-run, not an error. The two flags only matter
    when --apply is set."""

    fake = _fake_client()
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=fake):
        rc = run_reset_cli(_args(apply=False, confirmed=True))

    assert rc == EXIT_OK
    assert "(dry-run)" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Gate 3: dry-run by default
# ---------------------------------------------------------------------------


def test_default_invocation_is_dry_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No flags, dry-run shape, exit 0."""

    fake = _fake_client()
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=fake):
        rc = run_reset_cli(_args())

    assert rc == EXIT_OK
    err = capsys.readouterr().err
    assert "(dry-run)" in err


# ---------------------------------------------------------------------------
# Happy path: all three gates pass
# ---------------------------------------------------------------------------


def test_both_apply_and_confirmation_pass_the_gates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With both safety flags and a non-source URL, the command
    proceeds (currently to the placeholder for FEAT-37c). The
    test pins exit 0 and the "apply" notice in stderr."""

    fake = _fake_client()
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=fake):
        rc = run_reset_cli(_args(apply=True, confirmed=True))

    assert rc == EXIT_OK
    err = capsys.readouterr().err
    assert "(apply)" in err
