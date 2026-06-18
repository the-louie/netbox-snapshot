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
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from nbsnap.cli.common import add_audit_flags, add_scope_flags, add_tls_flags
from nbsnap.export.driver import DEFAULT_SCOPE
from nbsnap.http import NetboxHTTPError
from nbsnap.http.client import NetboxHTTP
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS

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
    # ARCH-10d: shared TLS and scope flag builders.
    add_tls_flags(parser)
    add_scope_flags(parser)
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
    # ARCH-10d: shared audit flag builder.
    add_audit_flags(parser)


def run_reset_cli(args: argparse.Namespace) -> int:
    """CLI entry point with the three safety gates layered in.

    Each gate exits with a distinct code so an operator script
    can distinguish "blocked by source guard" from "missing
    confirmation flag" from "real failure during deletion".

    Gates fire in order:

    1. Source-URL guard (`is_source()`), exit 4. Refuses to
       run when the destination URL matches `NB_SOURCE_URL`,
       which would mean the operator pointed the command at
       production by mistake.
    2. Apply-without-confirmation, exit 1. `--apply` alone is
       not enough; the operator must ALSO pass
       `--i-know-what-im-doing`. No interactive prompt so the
       command stays scriptable, but the two flags together
       make accidental invocation impossible.
    3. Dry-run by default. Without `--apply`, the command
       prints a skeleton notice and returns 0.

    Enumeration and delete logic come in FEAT-37c through
    FEAT-37e in subsequent commits.
    """

    # Build the client first. from_env() reads NB_DESTINATION_*
    # by default; the role is locked to "destination" so the
    # source guard fires automatically when --url is pointed at
    # the source. is_source() below is the explicit double-check
    # at the CLI boundary.
    http = NetboxHTTP.from_env(
        "destination",
        url=args.url,
        token=args.token,
        verify_tls=not args.no_verify_tls,
    )

    # Gate 1: source-URL guard. NetboxHTTP.is_source() returns
    # True when base_url matches NB_SOURCE_URL on a host:port
    # basis. Refuse before any GET so we never touch the wire
    # against production.
    if http.is_source():
        sys.stderr.write(
            "nbsnap reset-destination: refusing, destination URL "
            f"matches NB_SOURCE_URL ({http.base_url}). The source "
            "NetBox is read-only by policy (see CLAUDE.md).\n"
        )
        return EXIT_BLOCKED_BY_SOURCE_GUARD

    # Gate 2: --apply requires --i-know-what-im-doing. The two-
    # flag requirement makes a stray `--apply` in CI harmless.
    if args.apply and not args.confirmed:
        sys.stderr.write(
            "nbsnap reset-destination: --apply also requires "
            "--i-know-what-im-doing.\n"
            f"  destination: {http.base_url}\n"
            "  this command will issue DELETE requests against "
            "the in-scope endpoints.\n"
            "Re-run with both flags to proceed.\n"
        )
        return EXIT_NEEDS_APPLY_FLAGS

    # Resolve scope and the --keep exclusion set.
    scope = _resolve_scope(args.content_types)
    keep_names = set(args.keep or [])

    # Determine the iteration order. Reverse topological order
    # means children die before parents, so Django's PROTECT FKs
    # do not 409 because of in-scope dependents. For dry-run we
    # could iterate alphabetically (the order does not matter
    # without real DELETE calls), but using the same order in
    # both modes keeps the operator-visible output consistent.
    delete_order = _reverse_topological_order(http, scope)

    sys.stderr.write(
        "# nbsnap reset-destination "
        + ("(apply)" if args.apply and args.confirmed else "(dry-run)")
        + "\n"
    )
    quiet = bool(getattr(args, "quiet", False))
    failures: list[tuple[str, int, str]] = []
    audit_lines: list[str] = []
    for ct in delete_order:
        endpoint = CONTENT_TYPE_ENDPOINTS.get(ct)
        if endpoint is None:
            # Out-of-table content type; skip silently. This
            # keeps the command robust against plugin types
            # the operator widened the scope to include but we
            # do not know how to enumerate.
            continue
        ids = list(_enumerate_ids(http, endpoint, keep_names))
        total = len(ids)
        if args.apply and args.confirmed:
            # FEAT-50: progress lines need a stable opening
            # phrase so an operator skimming the log can pair
            # "N records to delete" with subsequent "k/N (p%)"
            # lines. Dry-run keeps the legacy "would delete"
            # phrasing so prior CI scripts continue to match.
            sys.stderr.write(f"  {ct}: {total} records to delete\n")
        else:
            sys.stderr.write(f"  {ct}: would delete {total} records\n")
            continue
        progress = _ResetProgress(ct, total, quiet=quiet)
        ct_failures, ct_audit = _delete_ids_with_audit(
            http,
            endpoint,
            ids,
            ct,
            on_batch=progress.tick,
        )
        progress.finish()
        audit_lines.extend(ct_audit)
        for rid, msg in ct_failures:
            failures.append((ct, rid, msg))
            if args.on_error == "stop":
                sys.stderr.write(f"  STOP, first failure: {ct} id={rid} {msg[:160]}\n")
                _flush_audit(args.audit_out, audit_lines)
                return EXIT_DELETE_FAILURES

    _flush_audit(args.audit_out, audit_lines)
    if failures:
        sys.stderr.write(f"  {len(failures)} per-record failures\n")
        return EXIT_DELETE_FAILURES
    return EXIT_OK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_scope(content_types_arg: str | None) -> set[str]:
    """Return the set of content types to clear.

    When `--content-types` is unset, fall back to the same
    DEFAULT_SCOPE the export driver uses (the renderer-minimum
    set). When set, parse a comma-separated list and trim
    whitespace. Empty entries are dropped silently so the
    operator can copy-paste a multi-line CSV without surprises.
    """

    if not content_types_arg:
        return set(DEFAULT_SCOPE)
    return {token.strip() for token in content_types_arg.split(",") if token.strip()}


def _reverse_topological_order(http: NetboxHTTP, scope: set[str]) -> list[str]:
    """Compute the delete order: children before parents.

    Reuses the planner that the import side uses, then reverses
    the result. Children land last in the import topological
    order; if we run that backwards, children land first in the
    delete order, which is exactly what Django's PROTECT FKs
    require to avoid 409 Conflict.

    Falls back to alphabetical order when the planner fails for
    any reason (network blip, malformed schema). The command
    will then still attempt deletions but may produce some
    in-scope 409s that the operator can sweep up on a second
    invocation.
    """

    # Lazy imports: these modules are heavy and only loaded
    # when the reset command actually runs.
    from nbsnap.graph import from_openapi
    from nbsnap.graph import plan as build_plan
    from nbsnap.schema.openapi import OpenAPI

    try:
        openapi = OpenAPI.fetch(http)
        graph = from_openapi(openapi, scope=scope)
        plan_obj = build_plan(graph)
    except Exception:  # noqa: BLE001 - fallback on any failure
        return sorted(scope)

    # Reverse the planner output, keep only scoped content types.
    return [ct for ct in reversed(plan_obj.order) if ct in scope]


def _delete_ids_with_audit(
    http: NetboxHTTP,
    endpoint: str,
    ids: list[int],
    content_type: str,
    on_batch: "Callable[[int], None] | None" = None,
) -> tuple[list[tuple[int, str]], list[str]]:
    """Delete every id, return `(failures, audit_lines)`.

    The plain `_delete_ids` is the simpler helper that returns
    only failures; this variant additionally accumulates one
    JSON line per id with the outcome:

      {"content_type": ct, "id": rid, "outcome": "deleted"}
      {"content_type": ct, "id": rid, "outcome": "deleted-fallback"}
      {"content_type": ct, "id": rid, "outcome": "failed", "message": "..."}

    `deleted` for the bulk-success path, `deleted-fallback` for
    the per-id rescue path, `failed` for rows that did not
    delete cleanly. The line shape stays stable so downstream
    tooling can parse audit.jsonl reliably.

    `on_batch` (FEAT-50) is called with the number of ids the
    current batch attempted to process, regardless of whether
    every id succeeded. The caller uses that to drive the
    progress emitter; a None call site keeps the helper usable
    in tests that do not care about progress.

    Note: this helper does NOT dedupe. Calling it twice for the
    same content type with overlapping ids would write the same
    rows to the audit twice. Each call site in `run_reset_cli`
    processes each content type exactly once, so dedupe is not
    needed in practice.
    """

    failures: list[tuple[int, str]] = []
    audit: list[str] = []
    for batch in _chunks(ids, BATCH):
        if not batch:
            continue
        try:
            _bulk_delete(http, endpoint, batch)
            for rid in batch:
                audit.append(
                    json.dumps(
                        {"content_type": content_type, "id": rid, "outcome": "deleted"},
                        sort_keys=True,
                    )
                )
            if on_batch is not None:
                on_batch(len(batch))
            continue
        except NetboxHTTPError as exc:
            if 500 <= exc.status < 600:
                msg = f"bulk {exc.status}: {exc.body[:160]}"
                for rid in batch:
                    failures.append((rid, msg))
                    audit.append(
                        json.dumps(
                            {
                                "content_type": content_type,
                                "id": rid,
                                "outcome": "failed",
                                "message": msg,
                            },
                            sort_keys=True,
                        )
                    )
                if on_batch is not None:
                    on_batch(len(batch))
                continue
            # 4xx: per-id fallback.
            for rid in batch:
                try:
                    http._request("DELETE", f"{endpoint}{rid}/")
                except NetboxHTTPError as one_exc:
                    failures.append((rid, str(one_exc)))
                    audit.append(
                        json.dumps(
                            {
                                "content_type": content_type,
                                "id": rid,
                                "outcome": "failed",
                                "message": str(one_exc)[:200],
                            },
                            sort_keys=True,
                        )
                    )
                else:
                    audit.append(
                        json.dumps(
                            {
                                "content_type": content_type,
                                "id": rid,
                                "outcome": "deleted-fallback",
                            },
                            sort_keys=True,
                        )
                    )
            if on_batch is not None:
                on_batch(len(batch))
    return failures, audit


class _ResetProgress:
    """Emit 10%-boundary progress lines for one content type.

    The reset's per-content-type loop deletes in batches of
    `BATCH` ids. After every batch the caller hands the number
    of ids the batch covered to `tick`, and the emitter prints
    one `<ct>: <k>/<N> (<p>%)` line each time `k/N` crosses a
    new 10% boundary. `finish` always emits a closing 100% line
    (or a `done` line for empty content types) so an operator
    can pair the opening "to delete" line with a closing
    boundary.

    `quiet=True` suppresses per-percentage lines but keeps the
    closing line. The opening "<ct>: N records to delete" line
    lives in the caller because it is also printed in dry-run.
    """

    # Below this size, 10%-boundary granularity would print N
    # redundant lines for a section the operator can read in
    # one glance. The opening "N records to delete" line in the
    # caller and the closing `finish` line carry enough signal.
    _SMALL_N = 10

    def __init__(self, content_type: str, total: int, *, quiet: bool = False) -> None:
        self.ct = content_type
        self.total = total
        self.quiet = quiet
        self.done = 0
        self.next_boundary = 10  # next 10%-boundary to announce

    def tick(self, processed: int) -> None:
        """Advance the counter by `processed` and emit progress
        lines for every 10% boundary crossed.

        A single tick that crosses several boundaries (e.g. a
        big batch on a small content type) emits every crossed
        boundary, not just the final one, so the log stays
        deterministic regardless of batch size.

        Suppresses output when `total < _SMALL_N` (the section
        is too small for per-percentage granularity to add
        information) or when `quiet` is set.
        """

        self.done = min(self.done + processed, self.total)
        if self.total < self._SMALL_N or self.quiet:
            return
        while self.next_boundary <= 100:
            threshold = (self.total * self.next_boundary + 99) // 100
            # Ceiling division: at N=10, the 10% boundary lands
            # exactly on the 1st row (`(10*10+99)//100 = 1`), so
            # a 1-id tick announces 10%. Without ceil, 0.99
            # would round down to 0 and announce too early.
            if self.done < threshold:
                break
            if self.next_boundary == 100:
                # The closing 100% line is owned by `finish`
                # so a content type whose final batch exactly
                # hits N does not double-print 100%.
                break
            sys.stderr.write(f"  {self.ct}: {self.done}/{self.total} ({self.next_boundary}%)\n")
            self.next_boundary += 10

    def finish(self) -> None:
        """Emit the closing line for the content type.

        Three cases:

        * `total == 0`: print `0/0 (done)` so the operator sees
          an explicit end of the section instead of silence.
        * `total < _SMALL_N`: print `N/N (done)` — per-percentage
          lines were suppressed in `tick`, but the section still
          needs a paired closing line so the log structure stays
          consistent.
        * Otherwise: print one `100%` line. We do this here
          (not in `tick`) so the boundary is announced exactly
          once, even when the final tick coincides with the
          100% threshold.
        """

        if self.total <= 0:
            sys.stderr.write(f"  {self.ct}: 0/0 (done)\n")
            return
        if self.total < self._SMALL_N:
            sys.stderr.write(f"  {self.ct}: {self.total}/{self.total} (done)\n")
            return
        sys.stderr.write(f"  {self.ct}: {self.total}/{self.total} (100%)\n")


def _flush_audit(path: Path | None, lines: list[str]) -> None:
    """Write `lines` to `path` if `path` is set, else no-op.

    Each line is already a self-contained JSON object, so the
    write is a simple newline-joined dump. The parent directory
    is created so `--audit-out ~/audit/2026-06-15.jsonl` works
    without a manual mkdir.

    Memory note: the audit list is held in RAM across the whole
    run and flushed in one write at the end (or at the
    `--on-error stop` exit). For a 5,000-row run this is around
    500 KB, well within operator-host budgets. A streaming
    rewrite is the right move only if we ever need to clear a
    NetBox with hundreds of thousands of records.
    """

    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _delete_ids(http: NetboxHTTP, endpoint: str, ids: list[int]) -> list[tuple[int, str]]:
    """Delete every id, return the per-id failure list.

    Strategy: try the bulk endpoint first. NetBox 4.x accepts
    a JSON array body `[{"id": 1}, {"id": 2}]` on the list
    endpoint and DELETEs every row in one round trip. When the
    bulk call raises (typical 409 case is "one row has a
    dependent outside the scope"), fall back to per-id DELETE
    against each row so the rest of the batch still completes.

    Returns a list of `(id, message)` pairs for rows that
    failed even after the per-id fallback. An empty list means
    everything deleted cleanly.
    """

    failures: list[tuple[int, str]] = []
    for batch in _chunks(ids, BATCH):
        if not batch:
            continue
        try:
            _bulk_delete(http, endpoint, batch)
            continue  # batch succeeded
        except NetboxHTTPError as exc:
            # On a 4xx the fault is per-row (a dependent FK, a
            # protected record, etc.); fall back to per-id so the
            # rest of the batch still has a chance. On a 5xx the
            # destination NetBox is unhappy and per-id retries
            # would dogpile the same server error; surface the
            # whole batch as failed and move on.
            if 500 <= exc.status < 600:
                for rid in batch:
                    failures.append((rid, f"bulk {exc.status}: {exc.body[:160]}"))
                continue
            for rid in batch:
                try:
                    http._request("DELETE", f"{endpoint}{rid}/")
                except NetboxHTTPError as one_exc:
                    failures.append((rid, str(one_exc)))
    return failures


def _bulk_delete(http: NetboxHTTP, endpoint: str, ids: list[int]) -> None:
    """Issue one NetBox bulk DELETE call.

    NetBox 4.x accepts an array body on the list endpoint and
    deletes every named row. On success NetBox returns 204
    No Content; on failure it returns 4xx with a body
    describing which row caused the rejection. We surface that
    via the existing NetboxHTTPError so the caller can decide
    whether to fall back.
    """

    body = [{"id": rid} for rid in ids]
    http._request("DELETE", endpoint, json=body)


def _chunks(items: list[int], size: int) -> Iterator[list[int]]:
    """Yield successive `size`-length slices of `items`."""

    for i in range(0, len(items), size):
        yield items[i : i + size]


def _enumerate_ids(
    http: NetboxHTTP,
    endpoint: str,
    keep_names: set[str],
) -> Iterator[int]:
    """Yield every id NetBox lists for `endpoint`, minus --keep matches.

    Paginated via `NetboxHTTP.get_all`, which follows the `next`
    link until exhausted. `--keep` matches against both `name`
    and `slug` because some NetBox content types only have one
    or the other in their list response.
    """

    for row in http.get_all(endpoint):
        rid = row.get("id")
        if not isinstance(rid, int):
            continue
        # NetBox surfaces `name` on most content types and
        # `slug` on the ones that have a slug (Site, DeviceRole,
        # Manufacturer, etc.). Match against both so an operator
        # can `--keep hall-d` regardless of which field carries
        # the value.
        name = row.get("name") or row.get("slug") or ""
        if name in keep_names:
            continue
        yield rid
