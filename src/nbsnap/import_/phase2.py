"""Phase-2 deferred-FK writer.

Walks the deferred-FK queue produced by the FEAT-36b look-ahead
resolver during Phase-1, issues one PATCH per entry to fill in
the cycle-closing FKs that could not be created in one shot.

The classic cycle this matters for:

    Device.primary_ip4 -> IPAddress
    IPAddress.assigned_object -> Interface
    Interface.device -> Device

You cannot POST a Device with primary_ip4 set because the
IPAddress does not exist yet. You cannot POST the IPAddress
with assigned_object set because the Interface does not exist
yet. The look-ahead resolver in FEAT-36b creates everything in
the chain that it can in one pass and pushes a `DeferredFK` onto
the queue whenever it hits the cycle. Phase-2 then issues one
PATCH per deferred entry to fill in the missing reference once
both endpoints exist.

Each PATCH carries exactly one field so the destination NetBox's
audit log records exactly what changed and why.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from nbsnap.http.client import NetboxHTTP, NetboxHTTPError
from nbsnap.import_.lookahead import DeferredFK
from nbsnap.import_.nk_index import NKIndex
from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS

logger = logging.getLogger(__name__)


@dataclass
class Phase2Summary:
    """Aggregate per-PATCH outcomes for the Phase-2 pass."""

    counts: Counter[str] = field(default_factory=Counter)
    failures: list[tuple[DeferredFK, str]] = field(default_factory=list)

    def is_clean(self) -> bool:
        """True iff the run completed without any per-PATCH failure."""
        return self.counts.get("failed", 0) == 0


def run_phase2(
    http: NetboxHTTP,
    deferred_queue: Iterable[DeferredFK],
    *,
    dest_index: NKIndex,
    registry: NKRegistry,
) -> Phase2Summary:
    """Walk the deferred queue, PATCH each cycle-closing FK.

    Three outcomes per entry, surfaced in `Phase2Summary.counts`:

    * `patched`, the child record was successfully PATCHed with
      the resolved target id.
    * `skipped`, either the child record or the target NK could
      not be found on the destination. The look-ahead resolver
      should have put both records on the destination during
      Phase-1, so a skip means an upstream upsert failed; the
      Phase-1 summary already surfaces that.
    * `failed`, the PATCH itself returned non-2xx. The body
      snippet is preserved on the failure tuple for diagnosis.

    The PATCH body always carries one field, the deferred FK.
    The destination's audit log gets a clean diff.
    """

    summary = Phase2Summary()

    for entry in deferred_queue:
        # Look up the child record's destination id (Phase-1 should
        # have created it without this FK already).
        child_id = dest_index.lookup(entry.child_content_type, entry.child_nk)
        if child_id is None:
            logger.warning(
                "Phase-2: child %s NK=%r not on destination, skipping",
                entry.child_content_type, entry.child_nk,
            )
            summary.counts["skipped"] += 1
            continue

        # Look up the target NK. ensure_built is idempotent so
        # repeated calls for the same content type are cheap.
        dest_index.ensure_built(http, registry, entry.target_content_type)
        target_id = dest_index.lookup(entry.target_content_type, entry.target_nk)
        if target_id is None:
            logger.warning(
                "Phase-2: target %s NK=%r still missing, skipping %s.%s",
                entry.target_content_type, entry.target_nk,
                entry.child_content_type, entry.field_name,
            )
            summary.counts["skipped"] += 1
            continue

        # Build the endpoint URL for the child record and PATCH
        # the deferred field. NetBox returns 200 OK with the full
        # updated record; we only care about the 2xx status.
        endpoint = CONTENT_TYPE_ENDPOINTS.get(entry.child_content_type)
        if endpoint is None:
            # Should not happen because the child was created
            # successfully in Phase-1, but defend against a
            # registry mismatch by skipping rather than crashing.
            summary.counts["skipped"] += 1
            continue

        try:
            http.patch(f"{endpoint}{child_id}/", {entry.field_name: target_id})
        except NetboxHTTPError as exc:
            logger.warning(
                "Phase-2 PATCH failed for %s id=%d field=%s: HTTP %d %s",
                entry.child_content_type, child_id, entry.field_name,
                exc.status, exc.body[:160],
            )
            summary.counts["failed"] += 1
            summary.failures.append((entry, str(exc)))
            continue

        summary.counts["patched"] += 1
        logger.info(
            "Phase-2 PATCH %s id=%d %s -> %s id=%d",
            entry.child_content_type, child_id, entry.field_name,
            entry.target_content_type, target_id,
        )

    return summary
