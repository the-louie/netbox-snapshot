"""FEAT-37a skeleton tests.

Three things this file pins:

1. `reset-destination` is wired into the top-level CLI parser
   and its help text lists every documented flag.
2. The TICKETS map carries the subcommand id so `nbsnap --help`
   surfaces the placeholder for in-progress work.
3. `run_reset_cli` returns EXIT_OK on a dry-run invocation with
   a stubbed `NetboxHTTP` client. Real behaviour comes in
   FEAT-37b through FEAT-37e.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from nbsnap.cli import TICKETS, _build_parser
from nbsnap.reset_cli import EXIT_OK, run_reset_cli


def _args(**override) -> argparse.Namespace:
    """Build an argparse.Namespace shaped like the real CLI emits.

    Tests override only the flags they exercise; everything else
    stays at the documented default so the namespace stays
    representative of a real invocation.
    """

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


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe NB_* env vars so the destination URL the test sets
    is the only signal from_env() sees."""

    for var in (
        "NB_SOURCE_URL",
        "NB_SOURCE_TOKEN",
        "NB_DESTINATION_URL",
        "NB_DESTINATION_TOKEN",
        "NB_URL",
        "NB_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def test_tickets_map_includes_reset_destination() -> None:
    """The top-level CLI knows the subcommand exists."""

    assert "reset-destination" in TICKETS
    assert TICKETS["reset-destination"] == "FEAT-37"


def test_top_level_help_lists_reset_destination() -> None:
    """`nbsnap --help` surfaces the subcommand alongside the others."""

    parser = _build_parser()
    help_text = parser.format_help()
    assert "reset-destination" in help_text


def test_reset_destination_help_lists_every_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The subcommand's own --help block lists every documented flag.

    Capture via the help action so we know the argparse wiring
    is complete even before any real behaviour lands.
    """

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["reset-destination", "--help"])
    captured = capsys.readouterr()
    out = captured.out

    # Every documented flag from add_reset_args should appear.
    for flag in (
        "--url",
        "--token",
        "--no-verify-tls",
        "--content-types",
        "--keep",
        "--apply",
        "--i-know-what-im-doing",
        "--on-error",
        "--audit-out",
    ):
        assert flag in out, f"missing flag in --help: {flag}"


def test_run_reset_cli_returns_ok_on_dry_run() -> None:
    """Skeleton mode returns EXIT_OK and does not raise.

    NetboxHTTP.from_env is stubbed so we do not require any
    network or real env vars during the unit test.
    """

    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=MagicMock()):
        rc = run_reset_cli(_args(apply=False, confirmed=False))
    assert rc == EXIT_OK


def test_run_reset_cli_passes_verify_tls_through() -> None:
    """The --no-verify-tls flag becomes verify_tls=False in the
    NetboxHTTP constructor call. Belt-and-braces check that the
    flag is wired even before FEAT-37b lands the safety logic.
    """

    seen: dict[str, object] = {}

    def fake_from_env(_role: str, **kwargs) -> MagicMock:
        seen.update(kwargs)
        return MagicMock()

    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", side_effect=fake_from_env):
        run_reset_cli(_args(no_verify_tls=True))
    assert seen["verify_tls"] is False


def test_run_reset_cli_constructs_destination_role() -> None:
    """from_env is called with `"destination"`, never `"source"`.

    Picking the wrong role would be a serious safety hole, the
    source NetBox is read-only by policy.
    """

    seen_role: dict[str, str] = {}

    def fake_from_env(role: str, **_kwargs) -> MagicMock:
        seen_role["role"] = role
        return MagicMock()

    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", side_effect=fake_from_env):
        run_reset_cli(_args())
    assert seen_role["role"] == "destination"


def test_top_level_help_mentions_subcommand_name() -> None:
    """The synopsis line in `nbsnap --help` carries the subcommand
    name so operators discover the command by reading help."""

    parser = _build_parser()
    help_text = parser.format_help()
    # The subparsers block lists each subcommand by its
    # registered name; presence in help_text is the assertion.
    assert "reset-destination" in help_text
