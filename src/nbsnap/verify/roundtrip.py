"""Round-trip harness (FEAT-27a/b).

Exports the source, imports into the destination, re-exports the
destination, diffs the two exports. The harness is a Python
function so tests can call it directly; the CLI is a thin shell
that surfaces flags and prints a summary.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nbsnap.export.driver import run_export
from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.driver import run_import
from nbsnap.schema.status import VersionSkew
from nbsnap.verify.diff import DEFAULT_EXCLUSIONS, TreeDiff, diff_trees


@dataclass
class RoundTripResult:
    """Aggregate result of the round-trip harness."""

    source_snapshot: Path
    dest_snapshot: Path
    diff: TreeDiff


def round_trip(
    source: NetboxHTTP, destination: NetboxHTTP, workdir: Path | None = None
) -> RoundTripResult:
    """Run the full source -> snapshot -> destination -> snapshot loop."""

    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="nbsnap-roundtrip-"))
    src_snap = workdir / "source"
    dst_snap = workdir / "destination"

    run_export(source, src_snap)
    run_import(destination, src_snap, max_skew=VersionSkew.MINOR, on_error="continue")
    run_export(destination, dst_snap)

    return RoundTripResult(
        source_snapshot=src_snap,
        dest_snapshot=dst_snap,
        diff=diff_trees(src_snap, dst_snap, DEFAULT_EXCLUSIONS),
    )


def add_verify_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-url", help="source NetBox URL; defaults to NB_SOURCE_URL"
    )
    parser.add_argument("--source-token", help="defaults to NB_SOURCE_TOKEN")
    parser.add_argument("--dest-url", help="defaults to NB_DESTINATION_URL")
    parser.add_argument("--dest-token", help="defaults to NB_DESTINATION_TOKEN")
    parser.add_argument(
        "--no-verify-tls",
        action="store_true",
        help="disable TLS verification for both endpoints",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="optional persistent working directory",
    )


def run_verify(args: argparse.Namespace) -> int:
    source = NetboxHTTP.from_env(
        "source",
        url=args.source_url,
        token=args.source_token,
        verify_tls=not args.no_verify_tls,
    )
    destination = NetboxHTTP.from_env(
        "destination",
        url=args.dest_url,
        token=args.dest_token,
        verify_tls=not args.no_verify_tls,
    )
    result = round_trip(source, destination, args.workdir)
    sys.stderr.write(f"# round-trip workdir: {result.source_snapshot.parent}\n")
    if result.diff.is_clean():
        sys.stderr.write("snapshots match\n")
        return 0
    sys.stderr.write("snapshots differ:\n")
    for fd in result.diff.file_diffs:
        if fd.rows_only_left or fd.rows_only_right or fd.rows_changed:
            sys.stderr.write(
                f"  {fd.path}: only_left={len(fd.rows_only_left)} "
                f"only_right={len(fd.rows_only_right)} "
                f"changed={len(fd.rows_changed)}\n"
            )
    return 1
