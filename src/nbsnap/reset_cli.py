"""`nbsnap reset-destination` subcommand.

Wipes every in-scope object from the destination NetBox so the
next `nbsnap import` can run from a clean slate. Designed for
operator-driven test loops and rescue iteration, NOT for routine
production maintenance.

Three independent safety layers, layered defence in depth:

1. Source-URL guard rail. The constructor and request envelope
   in `NetboxHTTP` already refuse non-GET against
   `NB_SOURCE_URL`, and we add an explicit `is_source()` check
   at the CLI boundary so the operator sees a clear refusal
   message before any GET fires.
2. Dry-run is the default. Without `--apply`, the command
   prints what it would delete and exits 0 with zero side
   effects.
3. `--apply` requires `--i-know-what-im-doing` alongside.
   Without the second flag, the command refuses even with
   `--apply`. No interactive prompt, so the command is safe to
   script in CI but is impossible to invoke destructively by
   accident.

This module is the FEAT-37a skeleton. The safety layers,
enumeration, bulk-delete, and audit logic are added by FEAT-37b
through FEAT-37e in subsequent commits. For now `run_reset_cli`
returns OK without doing anything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nbsnap.http.client import NetboxHTTP

# Documented exit codes. The numbers stay stable across follow-up
# tickets so a CI script can `if [ "$?" -eq 4 ]` reliably.
EXIT_OK = 0
EXIT_NEEDS_APPLY_FLAGS = 1
EXIT_DELETE_FAILURES = 2
EXIT_BLOCKED_BY_SOURCE_GUARD = 4

# Bulk DELETE batch size. NetBox 4.x accepts arrays on the list
# endpoint; 100 keeps payloads well under typical front-proxy
# body limits and lets one bad row affect at most 99 siblings.
BATCH = 100


def add_reset_args(parser: argparse.ArgumentParser) -> None:
    """Wire the reset-destination subcommand's argument surface.

    Mirrors the conventions used by `nbsnap import` and `nbsnap
    export`: URL / token / TLS / scope flags first, then the
    destructive flags grouped at the end so they stand out in
    `--help` output.
    """

    parser.add_argument(
        "--url",
        help="NetBox base URL; defaults to NB_DESTINATION_URL",
    )
    parser.add_argument(
        "--token",
        help="NetBox API token; defaults to NB_DESTINATION_TOKEN",
    )
    parser.add_argument(
        "--no-verify-tls",
        action="store_true",
        help="disable TLS verification (self-signed dests only)",
    )
    parser.add_argument(
        "--content-types",
        help=(
            "comma-separated content types to clear; defaults to "
            "the renderer-minimum scope used by `nbsnap import`"
        ),
    )
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="<name-or-slug>",
        help=(
            "exclude any record whose name or slug matches; "
            "repeatable, useful for pinning seed objects"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually issue DELETE calls; default is dry-run",
    )
    parser.add_argument(
        "--i-know-what-im-doing",
        action="store_true",
        dest="confirmed",
        help="required alongside --apply; no interactive prompt",
    )
    parser.add_argument(
        "--on-error",
        choices=["stop", "continue"],
        default="stop",
        help="behaviour when a single DELETE fails (default stop)",
    )
    parser.add_argument(
        "--audit-out",
        type=Path,
        default=None,
        help="write a per-record JSONL audit of deletes to this path",
    )


def run_reset_cli(args: argparse.Namespace) -> int:
    """CLI entry point.

    Skeleton implementation for FEAT-37a: builds the NetboxHTTP
    client and reports zero work in dry-run shape. The real
    safety, enumeration, and delete logic land in FEAT-37b
    through FEAT-37e.
    """

    # Construct the client via from_env so the role-specific env
    # vars get picked up the same way `nbsnap import` picks them
    # up. The constructor itself attaches the source-URL guard.
    NetboxHTTP.from_env(
        "destination",
        url=args.url,
        token=args.token,
        verify_tls=not args.no_verify_tls,
    )

    # Dry-run shape that FEAT-37b through FEAT-37e will fill in.
    sys.stderr.write(
        "# nbsnap reset-destination (dry-run, FEAT-37a skeleton)\n"
    )
    sys.stderr.write(
        "  no enumeration or delete logic yet; FEAT-37c through "
        "FEAT-37e land those in subsequent commits\n"
    )
    return EXIT_OK
