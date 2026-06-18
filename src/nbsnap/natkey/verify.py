"""Duplicate-NK audit (FEAT-10a) and CLI wiring (FEAT-10b).

`verify-natkeys` is an operator-facing command that walks the
source NetBox, computes the natural key of every record using the
registry, and reports any content type where two records resolve
to the same NK. A duplicate is a sign that the registered NK
strategy does not actually identify records uniquely on this
source instance.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from nbsnap.http.client import NetboxHTTP
from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.registry import default
from nbsnap.natkey.resolver import NaturalKey, resolve
from nbsnap.schema.content_type import _ENDPOINTS, ContentType, InvalidContentTypeError

# ARCH-05b/e: the canonical endpoint mapping lives in
# :mod:`nbsnap.schema.content_type`. The assignment below re-exports
# it under the legacy public name so external callers (export/driver.py,
# reset_cli.py, several import_/ helpers) that still index the dict
# directly keep working unchanged. The assignment shape (not an aliased
# import) is what mypy with strict `--no-implicit-reexport` accepts as
# a deliberate re-export. Internal lookups in this module use
# :meth:`ContentType.endpoint` so the typed boundary lives here rather
# than at every consumer.
CONTENT_TYPE_ENDPOINTS = _ENDPOINTS


@dataclass(frozen=True)
class DuplicateFinding:
    """One duplicate NK observed against a single content type."""

    content_type: str
    natural_key: NaturalKey
    record_ids: tuple[int, ...]


@dataclass
class VerifyReport:
    """Aggregate result of the audit walker."""

    by_ct: dict[str, int] = field(default_factory=dict)
    duplicates: list[DuplicateFinding] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not self.duplicates


def audit(http: NetboxHTTP, registry: NKRegistry | None = None) -> VerifyReport:
    """Walk every supported endpoint and report duplicate NKs.

    Args:
        http: A client bound to the source NetBox. Read-only path.
        registry: Optional override; defaults to the standard
            `natkey.registry.default()`.

    Returns:
        A `VerifyReport` summarising counts per content type and
        any duplicates the auditor saw.
    """

    reg = registry or default()
    report = VerifyReport()

    for spec in reg:
        # ARCH-05e: use the typed boundary for internal lookups; an
        # unregistered content type produces InvalidContentTypeError
        # which we treat the same as the old None branch, skip and
        # let the caller see the gap.
        try:
            endpoint = ContentType.from_str(spec.content_type).endpoint()
        except InvalidContentTypeError:
            continue
        seen: dict[NaturalKey, list[int]] = defaultdict(list)
        count = 0
        for row in _iter_endpoint(http, endpoint):
            count += 1
            try:
                nk = resolve(reg, spec.content_type, row)
            except ValueError:
                # An NK that cannot be computed is a different kind
                # of failure; the audit ignores it here because the
                # main checker is duplicates, the missing-NK case is
                # surfaced by the export engine's hard failure path.
                continue
            seen[nk].append(int(row.get("id") or 0))
        report.by_ct[spec.content_type] = count
        for nk, ids in seen.items():
            if len(ids) > 1:
                report.duplicates.append(DuplicateFinding(spec.content_type, nk, tuple(ids)))

    return report


def _iter_endpoint(http: NetboxHTTP, endpoint: str) -> Iterator[dict[str, Any]]:
    """Wrap the http pagination so the auditor can mock it cleanly."""
    yield from http.get_all(endpoint)


def add_verify_natkeys_args(parser: argparse.ArgumentParser) -> None:
    """Wire the verify-natkeys sub-command arguments."""

    parser.add_argument("--url", help="NetBox base URL; defaults to NB_SOURCE_URL")
    parser.add_argument("--token", help="NetBox API token; defaults to NB_SOURCE_TOKEN")
    parser.add_argument("--no-verify-tls", action="store_true", help="disable TLS verification")


def run_verify_natkeys(args: argparse.Namespace) -> int:
    """CLI entry point: run the audit, render the report, set exit code."""

    http = NetboxHTTP.from_env(
        "source",
        url=args.url,
        token=args.token,
        verify_tls=not args.no_verify_tls,
    )
    report = audit(http)

    sys.stderr.write("# nbsnap verify-natkeys\n")
    for ct, count in sorted(report.by_ct.items()):
        sys.stderr.write(f"  {ct}: {count} records\n")
    if report.is_clean():
        sys.stderr.write("\nno duplicate natural keys found\n")
        return 0
    sys.stderr.write(f"\nfound {len(report.duplicates)} duplicate NK(s):\n")
    for finding in report.duplicates:
        sys.stderr.write(
            f"  {finding.content_type} NK={finding.natural_key!r} on ids {finding.record_ids}\n"
        )
    return 2
