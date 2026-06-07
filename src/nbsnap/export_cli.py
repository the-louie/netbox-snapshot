"""`nbsnap export` CLI plumbing (FEAT-17a/b).

Lightweight wrapper around `export.driver.run_export`. The CLI is
where we surface the URL/token plumbing, the resume flag, and the
output directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nbsnap.cli.common import add_scope_flags, add_tls_flags
from nbsnap.export.driver import DEFAULT_SCOPE, run_export
from nbsnap.http.client import NetboxHTTP


def add_export_args(parser: argparse.ArgumentParser) -> None:
    """Wire the export sub-command's arguments."""

    parser.add_argument("--url", help="NetBox base URL; defaults to NB_SOURCE_URL")
    parser.add_argument("--token", help="NetBox API token; defaults to NB_SOURCE_TOKEN")
    # ARCH-10b: TLS and scope flags come from cli.common so the
    # canonical name and help text are shared across subcommands.
    add_tls_flags(parser)
    add_scope_flags(parser)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="output snapshot directory",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip content types reported done in progress.jsonl",
    )


def run_export_cli(args: argparse.Namespace) -> int:
    """CLI entry point: build the client, run the driver, print a summary."""

    http = NetboxHTTP.from_env(
        "source",
        url=args.url,
        token=args.token,
        verify_tls=not args.no_verify_tls,
    )
    scope = (
        set(DEFAULT_SCOPE)
        if not args.content_types
        else {token.strip() for token in args.content_types.split(",") if token.strip()}
    )
    manifest = run_export(http, args.out, scope=scope, resume=args.resume)

    sys.stderr.write("# nbsnap export complete\n")
    sys.stderr.write(f"  snapshot: {args.out}\n")
    sys.stderr.write(f"  netbox: {manifest.netbox_version}\n")
    sys.stderr.write("  counts:\n")
    for ct, count in sorted(manifest.counts.items()):
        sys.stderr.write(f"    {ct}: {count}\n")
    sys.stderr.write(f"  deferred edges: {len(manifest.deferred_edges)}\n")
    return 0
