"""`nbsnap import` CLI plumbing (FEAT-25a/b).

Error model
-----------

The import CLI maps every plausible failure mode to a clear,
short error message and a stable exit code. The goal is that an
operator hitting any of these from the terminal sees one or two
lines of useful text instead of a Python traceback.

Exit codes:

* 0  success, no failures, no blocking pre-flight findings.
* 1  pre-flight blocked the run (version skew over tolerance, or
     missing content types on the destination).
* 2  the run started but at least one row failed to upsert.
* 3  bad invocation (missing snapshot, malformed manifest,
     missing required env, unreadable schema file).
* 4  unreachable / authentication / TLS error against the
     destination NetBox before pre-flight could complete.
* 5  unexpected internal error; the original exception is still
     printed for debugging.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import requests

from nbsnap.export.manifest import MANIFEST_FILENAME
from nbsnap.http.client import NetboxHTTP, NetboxHTTPError
from nbsnap.import_.driver import ImportSummary, run_import
from nbsnap.import_.upsert import UpsertOutcome
from nbsnap.schema.openapi import SCHEMA_PATH
from nbsnap.schema.status import VersionSkew

# Exit code constants so the tests and the docstring agree.
EXIT_OK = 0
EXIT_PREFLIGHT_BLOCKED = 1
EXIT_ROW_FAILURES = 2
EXIT_BAD_INVOCATION = 3
EXIT_DESTINATION_UNREACHABLE = 4
EXIT_UNEXPECTED = 5

logger = logging.getLogger(__name__)


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
    parser.add_argument(
        "--audit-out",
        type=Path,
        default=None,
        help="write the per-drop audit JSONL to this path "
             "(default: <snapshot_dir>/audit.jsonl)",
    )


def run_import_cli(args: argparse.Namespace) -> int:
    """CLI entry point with hardened error handling.

    Each early failure mode is handled before any write attempt so
    the operator sees a clear "this is what is wrong" line instead
    of a Python traceback.
    """

    # ------------------------------------------------------------------
    # Pre-validation, runs entirely offline
    # ------------------------------------------------------------------
    in_dir = args.in_dir
    invocation_error = _validate_invocation(in_dir, args)
    if invocation_error is not None:
        sys.stderr.write(f"nbsnap import: {invocation_error}\n")
        return EXIT_BAD_INVOCATION

    # ------------------------------------------------------------------
    # Client construction, traps missing env or bad URL
    # ------------------------------------------------------------------
    try:
        http = NetboxHTTP.from_env(
            "destination",
            url=args.url,
            token=args.token,
            verify_tls=not args.no_verify_tls,
        )
    except ValueError as exc:
        sys.stderr.write(f"nbsnap import: {exc}\n")
        return EXIT_BAD_INVOCATION

    max_skew = VersionSkew[args.max_version_skew.upper()]

    # ------------------------------------------------------------------
    # Run, catching the high-likelihood failure modes per category
    # ------------------------------------------------------------------
    try:
        summary = run_import(http, in_dir, max_skew=max_skew, on_error=args.on_error)
    except requests.exceptions.SSLError as exc:
        sys.stderr.write(
            "nbsnap import: TLS verification failed against the destination "
            f"({http.base_url}). Either the destination cert is invalid, or pass "
            f"--no-verify-tls if you know it is intentional. ({exc})\n"
        )
        return EXIT_DESTINATION_UNREACHABLE
    except requests.exceptions.ConnectionError as exc:
        sys.stderr.write(
            f"nbsnap import: cannot reach destination at {http.base_url} ({exc})\n"
        )
        return EXIT_DESTINATION_UNREACHABLE
    except NetboxHTTPError as exc:
        if exc.status in (401, 403):
            sys.stderr.write(
                "nbsnap import: authentication failed against the destination "
                f"({http.base_url}). Check NB_DESTINATION_TOKEN or --token. "
                f"HTTP {exc.status}.\n"
            )
            return EXIT_DESTINATION_UNREACHABLE
        sys.stderr.write(
            f"nbsnap import: destination returned HTTP {exc.status}: "
            f"{exc.body[:200]}\n"
        )
        return EXIT_DESTINATION_UNREACHABLE
    except (json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(
            f"nbsnap import: snapshot is unreadable, {type(exc).__name__}: {exc}\n"
        )
        return EXIT_BAD_INVOCATION
    except Exception as exc:  # noqa: BLE001 - last-resort catch
        sys.stderr.write(
            f"nbsnap import: unexpected error, please report. {type(exc).__name__}: {exc}\n"
        )
        return EXIT_UNEXPECTED

    # ------------------------------------------------------------------
    # Success path, render the summary
    # ------------------------------------------------------------------
    sys.stderr.write("# nbsnap import complete\n")
    sys.stderr.write(f"  preflight version skew: {summary.preflight.version_skew.name}\n")
    if summary.preflight.missing_content_types:
        sys.stderr.write(
            "  missing content types: "
            f"{sorted(summary.preflight.missing_content_types)}\n"
        )
    for outcome in (
        UpsertOutcome.CREATED,
        UpsertOutcome.UPDATED,
        UpsertOutcome.NOOP,
        UpsertOutcome.FAILED,
    ):
        sys.stderr.write(f"  {outcome.value}: {summary.counts.get(outcome, 0)}\n")
    sys.stderr.write(summary.auditor.render_summary())
    audit_path = args.audit_out or (in_dir / "audit.jsonl")
    summary.auditor.write_jsonl(audit_path)
    sys.stderr.write(f"  audit log: {audit_path}\n")
    if summary.phase2 is not None:
        sys.stderr.write(
            f"  phase2: patched={summary.phase2.counts.get('patched', 0)} "
            f"skipped={summary.phase2.counts.get('skipped', 0)} "
            f"failed={summary.phase2.counts.get('failed', 0)}\n"
        )
    return _compute_exit_code(summary, max_skew)


def _compute_exit_code(summary: ImportSummary, max_skew: VersionSkew) -> int:
    """Map a fully-categorised ImportSummary to a CLI exit code.

    FEAT-36f sharpens the contract: a single `nbsnap import`
    invocation is expected to complete the destination, so the
    exit code reflects only **real failures**, not transient
    ordering noise.

    `EXIT_ROW_FAILURES` (2) fires when any of:

    * Phase-1 had upsert failures.
    * Phase-2 had PATCH failures.
    * The audit log carries `MISSING_FROM_SOURCE` drops, the
      source NetBox has a stale reference.

    `OUT_OF_SCOPE` and `DEFERRED_TO_PHASE2` drops do NOT
    contribute, they are expected behaviour of the network-only
    scope and the cycle-breaker respectively.

    `EXIT_PREFLIGHT_BLOCKED` (1) fires when preflight refused
    the run, takes precedence over row-failure exit codes
    because the import never actually ran in that case.
    """
    if summary.preflight.is_blocking(max_skew):
        return EXIT_PREFLIGHT_BLOCKED

    from nbsnap.import_.audit import DropCategory

    phase2_failures = (
        summary.phase2.counts.get("failed", 0)
        if summary.phase2 is not None
        else 0
    )
    missing_from_source = sum(
        1 for ev in summary.auditor.events
        if ev.category is DropCategory.MISSING_FROM_SOURCE
    )
    if summary.failures or phase2_failures or missing_from_source:
        if summary.failures:
            sys.stderr.write(
                f"  first failure: {summary.failures[0].message}\n"
            )
        return EXIT_ROW_FAILURES
    return EXIT_OK


def _validate_invocation(in_dir: Path, args: argparse.Namespace) -> str | None:
    """Pre-check the snapshot directory before opening any sockets.

    Returns a human-readable error string when something is wrong,
    or `None` to mean "all good, proceed".
    """

    if not in_dir.exists():
        return f"snapshot directory not found: {in_dir}"
    if not in_dir.is_dir():
        return f"--in must be a directory, got: {in_dir}"

    manifest_path = in_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return f"manifest missing from snapshot, expected at {manifest_path}"
    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"manifest at {manifest_path} is not valid JSON: {exc}"
    if not isinstance(manifest_data, dict):
        return f"manifest at {manifest_path} is not a JSON object"
    if "counts" not in manifest_data:
        return f"manifest at {manifest_path} is missing the required `counts` field"

    schema_path = in_dir / SCHEMA_PATH
    if not schema_path.is_file():
        return f"schema missing from snapshot, expected at {schema_path}"
    try:
        schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"schema at {schema_path} is not valid JSON: {exc}"
    if not isinstance(schema_data, dict) or "paths" not in schema_data:
        return f"schema at {schema_path} is not a valid OpenAPI document"

    # --max-version-skew is constrained by argparse choices already;
    # nothing else to validate.
    _ = args
    return None
