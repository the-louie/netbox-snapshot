"""Implementation of the `nbsnap plan` sub-command (FEAT-07a/b).

The plan sub-command fetches the source schema, builds the graph,
runs the planner, and prints a human-readable summary to stderr
plus a machine-readable form to stdout when `--json` is passed.

The default scope is the renderer-minimum set documented in
`docs/02-data-model-scope.md`. The operator can narrow further
with `--only` (comma-separated content types).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from typing import TextIO

from nbsnap.cli.common import add_scope_flags, add_tls_flags
from nbsnap.graph import Plan, from_openapi, plan
from nbsnap.http.client import NetboxHTTP
from nbsnap.schema.openapi import OpenAPI

# Renderer-minimum scope, mirrors the table in CLAUDE.md / TODO.md.
DEFAULT_SCOPE: frozenset[str] = frozenset(
    {
        "dcim.site",
        "dcim.location",
        "dcim.rack",
        "dcim.devicerole",
        "dcim.devicetype",
        "dcim.manufacturer",
        "dcim.device",
        "dcim.interface",
        "dcim.cable",
        "ipam.vlan",
        "ipam.prefix",
        "ipam.iprange",
        "ipam.ipaddress",
        "ipam.role",
        "extras.customfield",
        "extras.tag",
    }
)


def add_plan_args(parser: argparse.ArgumentParser) -> None:
    """Wire the plan-sub-command's arguments.

    Shared between `cli.py` and the standalone test entry point so
    the schema stays consistent.
    """

    parser.add_argument("--url", help="NetBox base URL; defaults to NB_SOURCE_URL")
    parser.add_argument("--token", help="NetBox API token; defaults to NB_SOURCE_TOKEN")
    # ARCH-10d: shared TLS and scope flag builders so the canonical
    # names and help text are identical across subcommands.
    add_tls_flags(parser)
    add_scope_flags(parser)
    parser.add_argument(
        "--json", dest="emit_json", action="store_true", help="machine-readable JSON to stdout"
    )


def _parse_scope(only: str | None) -> set[str]:
    if not only:
        return set(DEFAULT_SCOPE)
    return {token.strip() for token in only.split(",") if token.strip()}


def _print_human_plan(plan_obj: Plan, scope: Iterable[str], stream: TextIO) -> None:
    """Render the plan to a human-readable stream (stderr by convention)."""

    stream.write(f"# nbsnap plan\nscope: {len(list(scope))} content types\n\n")
    stream.write(f"deferred edges: {len(plan_obj.deferred)}\n")
    for edge in plan_obj.deferred:
        stream.write(
            f"  {edge.child}.{edge.field} -> {edge.parent} "
            f"(nullable={edge.nullable}, m2m={edge.is_m2m})\n"
        )
    stream.write(f"\nimport order ({len(plan_obj.order)} steps):\n")
    for i, ct in enumerate(plan_obj.order, 1):
        stream.write(f"  {i:>3}. {ct}\n")


def run_plan(args: argparse.Namespace) -> int:
    """Entry point used by the CLI dispatcher."""

    http = NetboxHTTP.from_env(
        "source",
        url=args.url,
        token=args.token,
        verify_tls=not args.no_verify_tls,
    )
    openapi = OpenAPI.fetch(http)
    scope = _parse_scope(args.content_types)
    graph = from_openapi(openapi, scope=scope)
    p = plan(graph)

    if args.emit_json:
        payload = {
            "scope": sorted(scope),
            "order": p.order,
            "deferred": [
                {
                    "child": e.child,
                    "parent": e.parent,
                    "field": e.field,
                    "nullable": e.nullable,
                    "is_m2m": e.is_m2m,
                }
                for e in p.deferred
            ],
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _print_human_plan(p, scope, sys.stderr)
    return 0
