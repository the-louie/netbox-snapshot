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
import signal
import sys
from pathlib import Path
from typing import Any

from nbsnap.cli.common import add_audit_flags, add_tls_flags
from nbsnap.http.client import NetboxHTTP, NetboxHTTPError
from nbsnap.http.exceptions import (
    SnapshotAuthError,
    SnapshotConnectivityError,
)
from nbsnap.import_.audit import DropCategory
from nbsnap.import_.driver import ImportSummary, run_import
from nbsnap.import_.phase2 import Phase2Outcome
from nbsnap.import_.upsert import UpsertOutcome
from nbsnap.schema.openapi import SCHEMA_PATH
from nbsnap.schema.status import VersionSkew
from nbsnap.snapshot import MANIFEST_FILENAME

# Exit code constants so the tests and the docstring agree.
EXIT_OK = 0
EXIT_PREFLIGHT_BLOCKED = 1
EXIT_ROW_FAILURES = 2
EXIT_BAD_INVOCATION = 3
EXIT_DESTINATION_UNREACHABLE = 4
EXIT_UNEXPECTED = 5
# FEAT-41: SKIPPED rows over a configured threshold. Distinct
# from EXIT_ROW_FAILURES because SKIPPED is "data did not
# replicate" while FAILED is "data was rejected on write".
EXIT_SKIPPED_OVER_THRESHOLD = 6
# FEAT-49: the run completed but used --allow-enum-dict-bypass.
# CI gates can use this to flag a "rescued via coerce" status
# without treating it as a failure. The bypass-used exit takes
# precedence over EXIT_OK only; row failures and skip-threshold
# breaches still win.
EXIT_BYPASS_USED = 7

logger = logging.getLogger(__name__)


def add_import_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", help="NetBox base URL; defaults to NB_DESTINATION_URL")
    parser.add_argument("--token", help="NetBox API token; defaults to NB_DESTINATION_TOKEN")
    # ARCH-10c: TLS and audit flags come from cli.common so the
    # canonical names and help text are shared across subcommands.
    add_tls_flags(parser)
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
    # ARCH-10c: shared --audit-out + --audit-fsync builder.
    add_audit_flags(parser)
    parser.add_argument(
        "--bypass-out",
        type=Path,
        default=None,
        help="(SEC-06a) write the preflight-bypass detail JSONL to this path "
        "(default: <snapshot_dir>/preflight-bypass.jsonl). Independent "
        "of --audit-out so the bypass record stays next to the snapshot "
        "even when audit output is redirected elsewhere.",
    )
    parser.add_argument(
        "--allow-enum-dict-bypass",
        action="store_true",
        help="(power user) proceed even if the snapshot carries "
        "the legacy {value, label} enum shape on a field. The "
        "import-side coerce should still recover, but the "
        "snapshot will not round-trip cleanly.",
    )
    parser.add_argument(
        "--max-parse-errors",
        type=int,
        default=0,
        help="exit with EXIT_ROW_FAILURES when the snapshot has "
        "more than N malformed JSONL rows; default 0 (any "
        "parse error fails the run)",
    )
    parser.add_argument(
        "--audit-summary-limit",
        type=int,
        default=10,
        help="show at most N top-offending (content_type, field) "
        "lines in the end-of-run audit block; default 10. "
        "The full set is always available in the audit log.",
    )
    parser.add_argument(
        "--max-skipped",
        type=int,
        default=-1,
        help="exit EXIT_SKIPPED_OVER_THRESHOLD (6) when the run "
        "skipped more than N rows in total; default -1 means "
        "unbounded. Per-content-type overrides via "
        "--max-skipped-<content_type> always take precedence.",
    )
    parser.add_argument(
        "--max-skipped-ct",
        action="append",
        default=[],
        metavar="<content_type>=<N>",
        help="per-content-type skip threshold, e.g. "
        "`--max-skipped-ct ipam.ipaddress=5`. Repeatable. "
        "Triggers EXIT_SKIPPED_OVER_THRESHOLD (6) when any "
        "listed content type's SKIPPED count exceeds its N.",
    )
    parser.add_argument(
        "--no-phase2-verify",
        action="store_true",
        help="trust the 2xx response of every Phase-2 PATCH "
        "without inspecting the returned field. Default is "
        "to verify (BUG-07).",
    )
    parser.add_argument(
        "--no-timestamps",
        action="store_true",
        help="omit HH:MM:SS prefixes from progress output. "
        "Default on; useful for log aggregators that add "
        "their own timestamps.",
    )
    parser.add_argument(
        "--no-lookahead-failure-cache",
        action="store_true",
        help="disable the cache that short-circuits a look-ahead "
        "after the destination has refused a parent's "
        "create. Useful when the destination's refusal is "
        "transient and the operator wants every sibling to "
        "retry. See FEAT-45b.",
    )
    parser.add_argument(
        "--plugins-dir",
        type=Path,
        default=None,
        help=(
            "ARCH-04c: directory of plugin .py files to load. Each file's "
            "module-level `plugin` object is registered through the public "
            "Registrar surface. Falls back to the `NBSNAP_PLUGINS_DIR` env "
            "variable when this flag is omitted."
        ),
    )
    parser.add_argument(
        "--strict-schema",
        action="store_true",
        help="exit EXIT_PREFLIGHT_BLOCKED when the destination's "
        "OpenAPI schema differs from the snapshot's at any "
        "in-scope (content_type, field) FK shape. Default "
        "is informational. See FEAT-46c.",
    )
    parser.add_argument(
        "--use-destination-schema",
        action="store_true",
        help="resolve FKs against the destination's OpenAPI "
        "(fetched at preflight) instead of the snapshot's. "
        "Useful when the destination has drifted to a "
        "newer NetBox and the snapshot's schema is stale. "
        "See FEAT-46c.",
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
    # Path-based progress wiring (REFACTOR-07). The driver
    # constructs the ProgressReporter internally after it
    # builds the summary, so the reporter is born with a live
    # auditor handle. Hard-kill safety still works because the
    # reporter flushes audit.jsonl on a 5-second cadence
    # (FEAT-43); --audit-fsync forces stable-storage commit.
    audit_path = args.audit_out or (in_dir / "audit.jsonl")

    # FEAT-43: install SIGTERM/SIGINT handlers so external
    # supervisors (deploy restart, OOM monitor, operator
    # Ctrl-C) get a final audit flush before the process
    # exits. The handler re-raises by exiting non-zero
    # because the import did not complete; the partial audit
    # is preserved on disk for post-mortem.
    _install_termination_handlers(audit_path)

    try:
        summary = run_import(
            http,
            in_dir,
            max_skew=max_skew,
            on_error=args.on_error,
            allow_enum_dict_bypass=args.allow_enum_dict_bypass,
            progress_stream=sys.stderr,
            progress_audit_path=audit_path,
            progress_fsync=args.audit_fsync,
            progress_show_timestamps=not args.no_timestamps,
            phase2_verify=not args.no_phase2_verify,
            cache_lookahead_failures=not args.no_lookahead_failure_cache,
            strict_schema=args.strict_schema,
            use_destination_schema=args.use_destination_schema,
            plugins_dir=args.plugins_dir,
        )
    except SnapshotConnectivityError as exc:
        # ARCH-07c: ARCH-07b translates the bare requests exceptions
        # into SnapshotConnectivityError at the HTTP boundary. The
        # CLI branches on ``exc.reason`` to render the right hint:
        # a TLS failure points to ``--no-verify-tls``, a connection
        # or timeout failure points to URL/firewall.
        if exc.reason == "tls":
            sys.stderr.write(
                "nbsnap import: TLS verification failed against the destination "
                f"({http.base_url}). Either the destination cert is invalid, or pass "
                f"--no-verify-tls if you know it is intentional. ({exc})\n"
            )
        else:
            sys.stderr.write(
                f"nbsnap import: cannot reach destination at {http.base_url} ({exc})\n"
            )
        return EXIT_DESTINATION_UNREACHABLE
    except SnapshotAuthError as exc:
        # ARCH-07c: 401/403 used to live under the NetboxHTTPError
        # branch with an inline status check. ARCH-07b promoted them
        # into a dedicated exception so the catch is direct now.
        sys.stderr.write(
            "nbsnap import: authentication failed against the destination "
            f"({http.base_url}). Check NB_DESTINATION_TOKEN or --token. "
            f"HTTP {exc.status}.\n"
        )
        return EXIT_DESTINATION_UNREACHABLE
    except NetboxHTTPError as exc:
        sys.stderr.write(
            f"nbsnap import: destination returned HTTP {exc.status}: {exc.body[:200]}\n"
        )
        return EXIT_DESTINATION_UNREACHABLE
    except (json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(f"nbsnap import: snapshot is unreadable, {type(exc).__name__}: {exc}\n")
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
    if summary.preflight.unknown_content_types:
        # ARCH-08c: an unknown content type is a snapshot-side defect,
        # the manifest carries a type nbsnap does not recognise. Print
        # the offenders here so the operator-facing log mirrors the
        # in-process is_blocking decision the driver already made.
        sys.stderr.write(
            "  unknown content types (refused before any HTTP call): "
            f"{sorted(summary.preflight.unknown_content_types)}\n"
        )
    if summary.preflight.missing_content_types:
        sys.stderr.write(
            f"  missing content types: {sorted(summary.preflight.missing_content_types)}\n"
        )
    if summary.preflight.snapshot_format_issues:
        if args.allow_enum_dict_bypass:
            # FEAT-47: the operator already opted into the
            # bypass; reprinting the full per-file list is just
            # noise. Collapse to one summary line. The structured
            # detail still lands on disk via `bypass_out` so a
            # forensic inspector can reconstruct what coerced.
            sys.stderr.write(
                f"  enum-dict bypass active: "
                f"{len(summary.preflight.snapshot_format_issues)} "
                "files used the import-side coerce.\n"
            )
            # SEC-06a: the bypass detail belongs inside the snapshot
            # directory (not next to --audit-out). Co-locating it
            # with the snapshot keeps the forensic record paired with
            # the source artefact even when an operator redirects
            # audit output to a separate filesystem.
            bypass_path = args.bypass_out or (in_dir / "preflight-bypass.jsonl")
            with bypass_path.open("w", encoding="utf-8") as fp:
                for issue in summary.preflight.snapshot_format_issues:
                    fp.write(json.dumps(issue, sort_keys=True) + "\n")
            sys.stderr.write(f"  bypass detail: {bypass_path}\n")
        else:
            sys.stderr.write("nbsnap import: snapshot format issues detected:\n")
            for issue in summary.preflight.snapshot_format_issues[:10]:
                sys.stderr.write(f"  {_format_issue(issue)}\n")
            sys.stderr.write(
                "Re-export the snapshot with a current nbsnap, "
                "or pass --allow-enum-dict-bypass to proceed via "
                "the import-side coerce.\n"
            )
    if summary.preflight.schema_drift:
        # FEAT-46b: render the drift list compactly. The
        # operator should see the first ten lines and a
        # trailer for the rest.
        sys.stderr.write(
            f"  schema drift: {len(summary.preflight.schema_drift)} "
            "field(s) differ between snapshot and destination\n"
        )
        for entry in summary.preflight.schema_drift[:10]:
            sys.stderr.write(
                f"    {entry.content_type}.{entry.field} "
                f"snapshot={entry.snapshot_shape} "
                f"destination={entry.destination_shape}\n"
            )
        if len(summary.preflight.schema_drift) > 10:
            remaining = len(summary.preflight.schema_drift) - 10
            sys.stderr.write(f"    ... and {remaining} more\n")
    for outcome in (
        UpsertOutcome.CREATED,
        UpsertOutcome.UPDATED,
        UpsertOutcome.NOOP,
        UpsertOutcome.SKIPPED,
        UpsertOutcome.FAILED,
    ):
        sys.stderr.write(f"  {outcome.value}: {summary.counts.get(outcome, 0)}\n")
        if outcome is UpsertOutcome.SKIPPED and summary.skipped_by_ct:
            # FEAT-40: break the SKIPPED total out by content
            # type so a CI gate can apply per-bucket policy
            # without grepping audit.jsonl.
            for ct in sorted(summary.skipped_by_ct):
                reasons = summary.skipped_by_ct[ct]
                total = sum(reasons.values())
                if len(reasons) == 1:
                    only = next(iter(reasons))
                    sys.stderr.write(f"    {ct}: {total} ({only})\n")
                else:
                    pretty = ", ".join(f"{r}={n}" for r, n in sorted(reasons.items()))
                    sys.stderr.write(f"    {ct}: {total} ({pretty})\n")
    sys.stderr.write(summary.auditor.render_summary(limit=args.audit_summary_limit))
    # BUG-13 cross-check: the SKIPPED upsert count and the
    # number of `category=skipped` audit events must agree.
    # They diverge only on an emission bug; surface that to the
    # operator instead of silently miscounting.
    skipped_count = summary.counts.get(UpsertOutcome.SKIPPED, 0)
    audit_skipped_count = sum(
        1 for ev in summary.auditor.events if ev.category is DropCategory.SKIPPED
    )
    if skipped_count != audit_skipped_count:
        sys.stderr.write(
            f"  WARNING: skipped summary count ({skipped_count}) "
            f"diverges from audit skipped events "
            f"({audit_skipped_count})\n"
        )
    audit_path = args.audit_out or (in_dir / "audit.jsonl")
    summary.auditor.write_jsonl(audit_path)
    sys.stderr.write(f"  audit log: {audit_path}\n")
    if summary.phase2 is not None:
        sys.stderr.write(
            f"  phase2: patched={summary.phase2.counts.get(Phase2Outcome.PATCHED, 0)} "
            f"skipped={summary.phase2.counts.get(Phase2Outcome.SKIPPED, 0)} "
            f"failed={summary.phase2.counts.get(Phase2Outcome.FAILED, 0)} "
            f"verified_mismatch={summary.phase2.counts.get(Phase2Outcome.VERIFIED_MISMATCH, 0)}\n"
        )
    sys.stderr.write(f"  snapshot parse errors: {len(summary.parse_errors)}\n")
    for entry in summary.parse_errors[:5]:
        sys.stderr.write(f"    {entry['path']}:{entry['lineno']}: {entry['message']}\n")
    if len(summary.parse_errors) > 5:
        remaining = len(summary.parse_errors) - 5
        sys.stderr.write(f"    ... and {remaining} more\n")
    return _compute_exit_code(
        summary,
        max_skew,
        allow_enum_dict_bypass=args.allow_enum_dict_bypass,
        max_parse_errors=args.max_parse_errors,
        max_skipped=args.max_skipped,
        max_skipped_ct=_parse_max_skipped_ct(args.max_skipped_ct),
        strict_schema=args.strict_schema,
    )


_TERMINATION_HANDLER_INSTALLED = False


def _install_termination_handlers(audit_path: Path) -> None:
    """Wire SIGTERM and SIGINT to a one-shot best-effort flush.

    Re-installing the handler in the same process is a no-op
    (the global flag below tracks state). The handler is
    deliberately minimal: it logs, exits with a distinct
    non-zero code, and lets the runtime clean up the rest.
    FEAT-43: the ProgressReporter has already been flushing
    audit.jsonl on a 5s cadence, so on-disk state is at most
    5s stale even when this handler does not fire.
    """

    global _TERMINATION_HANDLER_INSTALLED
    if _TERMINATION_HANDLER_INSTALLED:
        return

    def _handler(signum: int, _frame: object) -> None:
        sys.stderr.write(
            f"\nnbsnap import: received signal {signum}; partial audit at {audit_path}\n"
        )
        sys.exit(130 if signum == signal.SIGINT else 143)

    # Test environments sometimes restrict signal binding; we
    # swallow ValueError because that is the failure mode when
    # the CLI is invoked from a non-main thread (e.g. in a
    # subprocess-test that imports run_import_cli directly).
    try:
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        _TERMINATION_HANDLER_INSTALLED = True
    except (ValueError, OSError):
        pass


def _parse_max_skipped_ct(values: list[str]) -> dict[str, int]:
    """Parse --max-skipped-ct flag values into a dict.

    Each value is `<content_type>=<N>`. Values that don't match
    are silently ignored, which avoids a noisy argparse failure
    for a user who mistypes a content type. The exit-code
    helper logs a warning when the dict ends up empty but the
    operator passed at least one --max-skipped-ct flag.
    """

    out: dict[str, int] = {}
    for raw in values:
        if "=" not in raw:
            continue
        ct, _, n = raw.partition("=")
        try:
            out[ct.strip()] = int(n)
        except ValueError:
            continue
    return out


def _format_issue(issue: dict[str, Any] | str) -> str:
    """Render a `snapshot_format_issues` entry as a human line.

    BUG-01a moved the report's issues from raw strings to
    `{path, field, rows_affected}` dicts. Old call sites that
    still pass a string (e.g. some tests) keep working
    unchanged.
    """

    if isinstance(issue, str):
        return issue
    path = issue.get("path", "<unknown>")
    field_name = issue.get("field", "<unknown>")
    rows = issue.get("rows_affected", 0)
    return (
        f"{path}: field {field_name!r} carries the "
        f"{{value, label}} enum-dict shape in {rows} row(s); the "
        f"snapshot was exported before FEAT-36-blocker landed"
    )


def _compute_exit_code(
    summary: ImportSummary,
    max_skew: VersionSkew,
    *,
    allow_enum_dict_bypass: bool = False,
    max_parse_errors: int = 0,
    max_skipped: int = -1,
    max_skipped_ct: dict[str, int] | None = None,
    strict_schema: bool = False,
) -> int:
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
    if summary.preflight.is_blocking(
        max_skew,
        allow_enum_dict_bypass=allow_enum_dict_bypass,
        strict_schema=strict_schema,
    ):
        return EXIT_PREFLIGHT_BLOCKED

    from nbsnap.import_.audit import DropCategory

    phase2_failures = (
        summary.phase2.counts.get(Phase2Outcome.FAILED, 0)
        + summary.phase2.counts.get(Phase2Outcome.VERIFIED_MISMATCH, 0)
        if summary.phase2 is not None
        else 0
    )
    missing_from_source = sum(
        1 for ev in summary.auditor.events if ev.category is DropCategory.MISSING_FROM_SOURCE
    )
    parse_errors_over_threshold = len(summary.parse_errors) > max_parse_errors
    if summary.failures or phase2_failures or missing_from_source or parse_errors_over_threshold:
        if summary.failures:
            sys.stderr.write(f"  first failure: {summary.failures[0].message}\n")
        return EXIT_ROW_FAILURES

    # FEAT-41: SKIPPED gating. Per-ct thresholds win over the
    # global threshold so an operator can say "fail on any
    # ipam.ipaddress skip, tolerate up to 10 dcim.cable skips"
    # with one flag invocation each.
    per_ct = max_skipped_ct or {}
    skipped_totals = {ct: sum(reasons.values()) for ct, reasons in summary.skipped_by_ct.items()}
    over_per_ct = any(skipped_totals.get(ct, 0) > limit for ct, limit in per_ct.items())
    total_skipped = sum(skipped_totals.values())
    over_global = max_skipped >= 0 and total_skipped > max_skipped
    if over_per_ct or over_global:
        return EXIT_SKIPPED_OVER_THRESHOLD
    # FEAT-49: distinct exit when the run completed via the
    # enum-dict bypass. The operator opted in but the audit
    # log shows BYPASS_COERCED events; the exit code keeps the
    # signal visible to a CI gate without parsing logs.
    if allow_enum_dict_bypass and any(
        ev.category.value == "bypass_coerced" for ev in summary.auditor.events
    ):
        return EXIT_BYPASS_USED
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
