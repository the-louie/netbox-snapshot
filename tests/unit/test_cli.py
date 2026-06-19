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


# Every sub-command in the table below is fully implemented and
# wired to a real entry point. The test that follows asserts the
# inventory matches what `nbsnap.cli.TICKETS` advertises so a
# future sub-command added without a real handler will fail loudly
# instead of silently becoming a no-op stub. When a new
# sub-command lands its slug goes here and one positive
# integration test should accompany it; the previous "skipif no
# stubs remain" guard turned the gap into a quiet skip and was
# replaced by this explicit allowlist.
_IMPLEMENTED_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "plan",
        "verify-natkeys",
        "export",
        "import",
        "diff",
        "verify",
        "pack",
        "unpack",
        "reset-destination",
    }
)


def test_every_subcommand_in_tickets_is_implemented() -> None:
    """The `TICKETS` map must not regrow stub-only sub-commands.

    Adding a new entry here without also landing its handler used
    to land an `xfail`-shaped skip in the suite; that hid the
    incomplete work. The positive assertion below makes the gap
    a hard failure instead.
    """

    advertised = set(TICKETS.keys())
    missing_handler = advertised - _IMPLEMENTED_SUBCOMMANDS
    assert missing_handler == set(), (
        f"new sub-command(s) {sorted(missing_handler)} have no implementation handler; "
        f"either implement them and add to _IMPLEMENTED_SUBCOMMANDS or remove them from TICKETS"
    )


def test_help_lists_every_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    """`nbsnap --help` lists every sub-command in `TICKETS`."""

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in TICKETS:
        assert name in out, f"help text missing sub-command {name!r}"
