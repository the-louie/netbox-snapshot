"""CLI surface tests for `nbsnap.cli`.

These tests pin down the Phase-0 contract: the parser exposes
`--version`, `--verbose`, `--quiet`, and every sub-command from the
`TICKETS` map. Each sub-command stub names a ticket on stderr and
exits 2 so an operator hitting an unfinished feature gets a clear
"track it here" pointer instead of a silent no-op.
"""

from __future__ import annotations

import pytest

from nbsnap import __version__
from nbsnap.cli import TICKETS, main


def test_version_flag_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    """`nbsnap --version` exits 0 and prints the dotted version."""

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_no_arguments_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    """Running `nbsnap` with no command prints help and exits 1."""

    rc = main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert "usage:" in captured.err.lower()


_REMAINING_STUBS = sorted(
    set(TICKETS.keys())
    - {
        "plan",
        "verify-natkeys",
        "export",
        "import",
        "diff",
        "verify",
        "pack",
        "unpack",
    }
)


@pytest.mark.skipif(
    not _REMAINING_STUBS, reason="all sub-commands implemented; no stubs remain"
)
@pytest.mark.parametrize("command", _REMAINING_STUBS or ["__none__"])
def test_stub_subcommand_reports_ticket(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Each stub sub-command exits 2 and names its tracking ticket."""

    rc = main([command])
    assert rc == 2
    err = capsys.readouterr().err
    assert TICKETS[command] in err, (
        f"stub for {command!r} should reference {TICKETS[command]!r}: {err!r}"
    )


def test_help_lists_every_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    """`nbsnap --help` lists every sub-command in `TICKETS`."""

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in TICKETS:
        assert name in out, f"help text missing sub-command {name!r}"
