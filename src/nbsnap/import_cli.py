"""`nbsnap import` CLI plumbing (FEAT-25a/b)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.driver import run_import
from nbsnap.import_.upsert import UpsertOutcome
from nbsnap.schema.status import VersionSkew


def add_import_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", help="NetBox base URL; defaults to NB_DESTINATION_URL")
    parser.add_argument("--token", help="NetBox API token; defaults to NB_DESTINATION_TOKEN")
    parser.add_argument("--no-verify-tls", action="store_true", help="disable TLS verification")
    parser.add_argument("--in", dest="in_dir", type=Path, required=True, help="snapshot directory")
    parser.add_argument(
        "--max-version-skew",
        choices=[skew.name.lower() for skew in VersionSkew],
        default="minor",
        help="largest allowed source/destination version gap",
    )
    parser.add_argument(
        "--on-error",
        choices=["stop", "continue"],
        default="stop",
        help="behaviour when a single row fails to upsert",
    )


def run_import_cli(args: argparse.Namespace) -> int:
    http = NetboxHTTP.from_env(
        "destination",
        url=args.url,
        token=args.token,
        verify_tls=not args.no_verify_tls,
    )
    max_skew = VersionSkew[args.max_version_skew.upper()]
    summary = run_import(http, args.in_dir, max_skew=max_skew, on_error=args.on_error)

    sys.stderr.write("# nbsnap import complete\n")
    sys.stderr.write(f"  preflight version skew: {summary.preflight.version_skew.name}\n")
    if summary.preflight.missing_content_types:
        sys.stderr.write(
            f"  missing content types: "
            f"{sorted(summary.preflight.missing_content_types)}\n"
        )
    for outcome in (
        UpsertOutcome.CREATED,
        UpsertOutcome.UPDATED,
        UpsertOutcome.NOOP,
        UpsertOutcome.FAILED,
    ):
        sys.stderr.write(f"  {outcome.value}: {summary.counts.get(outcome, 0)}\n")
    if summary.failures:
        sys.stderr.write(f"  first failure: {summary.failures[0].message}\n")
        return 2
    if summary.preflight.is_blocking(max_skew):
        return 1
    return 0
