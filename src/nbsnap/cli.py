"""Command-line entry point for `nbsnap`.

This module is intentionally thin during Phase 0. Each sub-command
prints a "not implemented yet" banner and exits with code 2, naming
the ticket that will land the real implementation. Wiring the
sub-parser surface up front means later phases can land their
features without breaking the CLI shape or any operator habit
already built up around it.

Exit codes follow the small convention used through the project:

* 0   success
* 1   user error (bad arguments, missing files, validation failure)
* 2   not implemented, the placeholder code used by every stub here
* 3+  reserved for future categories (e.g. partial run)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from nbsnap import __version__
from nbsnap.config import load_dotenv

# Map from sub-command name to the ticket that will deliver it.
# Keeping the mapping in one place makes the stub messages cheap to
# audit when a ticket id changes during planning.
TICKETS: dict[str, str] = {
    "export": "FEAT-17a",
    "import": "FEAT-25a",
    "plan": "FEAT-07a",
    "diff": "FEAT-26b",
    "verify": "FEAT-27b",
    "verify-natkeys": "FEAT-10b",
    "pack": "FEAT-34",
    "unpack": "FEAT-35",
}


def _stub(name: str) -> Callable[[argparse.Namespace], int]:
    """Build a handler that prints a stub message and exits 2.

    Each sub-command's handler is currently the same shape: tell the
    operator the feature is on the backlog, name the ticket, exit.
    A factory keeps the eight handlers from drifting apart.
    """

    def handler(_: argparse.Namespace) -> int:
        ticket = TICKETS[name]
        sys.stderr.write(
            f"nbsnap {name}: not implemented yet, tracked in {ticket}\n",
        )
        return 2

    return handler


def _build_parser() -> argparse.ArgumentParser:
    """Construct the full argument parser for `nbsnap`.

    Split out so tests can introspect the parser without going
    through `main`.
    """
    parser = argparse.ArgumentParser(
        prog="nbsnap",
        description=(
            "Portable NetBox snapshot tool. Exports the modelled "
            "network from a source NetBox and re-imports it into a "
            "destination NetBox, over the REST API only."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"nbsnap {__version__}",
    )

    # --verbose and --quiet are mutually exclusive sugar over the
    # logging level. Plumbing the actual logger lives in FEAT-28a.
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="raise log verbosity (INFO -> DEBUG)",
    )
    verbosity.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress log output below WARNING",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    # Each sub-command has the same minimal shape during Phase 0:
    # a name and a help string. Real argument surfaces land in their
    # owning FEAT- tickets.
    for name in TICKETS:
        sub = subparsers.add_parser(name, help=f"{name} a NetBox snapshot")
        if name == "plan":
            from nbsnap.plan_cli import add_plan_args, run_plan

            add_plan_args(sub)
            sub.set_defaults(func=run_plan)
        elif name == "verify-natkeys":
            from nbsnap.natkey.verify import add_verify_natkeys_args, run_verify_natkeys

            add_verify_natkeys_args(sub)
            sub.set_defaults(func=run_verify_natkeys)
        else:
            sub.set_defaults(func=_stub(name))

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Optional argument list, useful in tests. When `None`
            the parser falls back to `sys.argv[1:]`.

    Returns:
        The process exit code. The caller is expected to pass this
        to `sys.exit`.
    """
    # Auto-load `.env` before anything else so credential env vars
    # are visible to every code path below this line.
    load_dotenv()

    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 1

    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":  # pragma: no cover, exercised by the install entry point
    sys.exit(main())
