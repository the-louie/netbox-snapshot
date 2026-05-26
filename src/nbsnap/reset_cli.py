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
from collections.abc import Iterator
from pathlib import Path

from nbsnap.export.driver import DEFAULT_SCOPE
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

    # Enumerate every in-scope content type's record ids.
    # _enumerate_ids consults --keep so kept rows never enter
    # the delete pool. FEAT-37d will iterate the per-CT id
    # list with bulk DELETE; FEAT-37e will accumulate counts
    # for the summary.
    sys.stderr.write(
        "# nbsnap reset-destination "
        + ("(apply)" if args.apply and args.confirmed else "(dry-run)")
        + "\n"
    )
    for ct in sorted(scope):
        endpoint = CONTENT_TYPE_ENDPOINTS.get(ct)
        if endpoint is None:
            # Out-of-table content type; skip silently. This
            # keeps the command robust against plugin types
            # the operator widened the scope to include but we
            # do not know how to enumerate.
            continue
        ids = list(_enumerate_ids(http, endpoint, keep_names))
        verb = "deleting" if args.apply and args.confirmed else "would delete"
        sys.stderr.write(f"  {ct}: {verb} {len(ids)} records\n")
        # FEAT-37d will iterate `ids` here with bulk DELETE.

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


