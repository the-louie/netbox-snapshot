"""Demand-driven FK resolution with cycle detection.

The destination's NKIndex answers "is this record on the
destination right now?". When that misses, the look-ahead
resolver here consults the SnapshotIndex from FEAT-36a to ask
"is this record in the snapshot we are importing?". If yes, it
recursively upserts the missing target on the destination
first, then returns the new id so the outer FK resolves.

When the recursion meets a target that is already on the
processing stack, it has detected a cycle (Device.primary_ip4
to IPAddress to Interface to Device, for example). The cycle
cannot be created in one POST because each record needs the
others to exist first. The resolver pushes a `DeferredFK` onto
the queue the driver maintains and returns None so the outer
upsert proceeds without the cycle-closing field. Phase-2
(FEAT-36c, separate ticket) walks the queue and PATCHes each
deferred FK after Phase-1 has finished and both endpoints
exist.

This module is the FEAT-36b1 skeleton. The DeferredFK shape and
the MAX_DEPTH constant land here so later sub-tickets
(b2 destination tier, b3 snapshot tier, b4 cycle detection,
b5 driver wiring) can stack on a stable data shape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

# NaturalKey is the shared tuple-of-tuples alias used by every
# NK consumer. It lives in snapshot_index because the loader is
# the first module that needs to type it; treat that as the
# canonical home.
from nbsnap.import_.snapshot_index import NaturalKey

if TYPE_CHECKING:
    # TYPE_CHECKING-only imports break the runtime cycle:
    # lookahead is consumed by driver.py, driver consumes
    # upsert.py, and upsert needs NetboxHTTP / NKRegistry too.
    # At runtime we resolve the real classes lazily inside
    # resolve_or_create.
    from nbsnap.http.client import NetboxHTTP
    from nbsnap.import_.audit import Auditor
    from nbsnap.import_.nk_index import NKIndex
    from nbsnap.import_.snapshot_index import SnapshotIndex
    from nbsnap.natkey.model import NKRegistry
    from nbsnap.schema.openapi import OpenAPI

__all__ = ["DeferredFK", "MAX_DEPTH", "resolve_or_create"]

logger = logging.getLogger(__name__)

# Hard cap on recursive look-ahead depth. A real NetBox topology
# tops out at about five nested NK levels (IPAddress -> Interface
# -> Device -> Site has three nests; the polymorphic chain is
# the tallest). 200 is generously above that, so hitting the cap
# in practice is a strong signal of a malformed snapshot.
MAX_DEPTH = 200


@dataclass(frozen=True)
class DeferredFK:
    """One FK reference that the cycle detector decided to defer.

    The look-ahead resolver (FEAT-36b4) appends a DeferredFK
    whenever it detects that the parent record is being created
    right now further up the recursion stack. The driver walks
    the queue in Phase-2 (FEAT-36c) and PATCHes each entry once
    both endpoints exist on the destination.

    Frozen so the DeferredFK can be put in sets for dedupe and
    used as a dict key during Phase-2 processing.

    Fields:
        child_content_type: e.g. `dcim.device`. The record we
            were trying to create when the cycle was detected.
        child_nk: the natural key of that record. The driver
            uses it to look up the destination id during
            Phase-2.
        field_name: e.g. `primary_ip4`. The FK that was deferred.
        target_content_type: e.g. `ipam.ipaddress`. The other
            half of the cycle.
        target_nk: the NK of the target record. The driver
            resolves this against the now-complete NKIndex to
            get the destination id to PATCH in.
    """

    child_content_type: str
    child_nk: NaturalKey
    field_name: str
    target_content_type: str
    target_nk: NaturalKey


def resolve_or_create(
    http: NetboxHTTP,
    snapshot_index: SnapshotIndex,
    dest_index: NKIndex,
    registry: NKRegistry,
    *,
    content_type: str,
    natural_key: NaturalKey,
    processing_stack: set[tuple[str, NaturalKey]],
    deferred_queue: list[DeferredFK],
    depth: int = 0,
    openapi: OpenAPI | None = None,
    auditor: Auditor | None = None,
    failed_keys: set[tuple[str, NaturalKey]] | None = None,
    deferred_fields_by_ct: dict[str, set[str]] | None = None,
) -> int | None:
    """Resolve a target NK to a destination id, creating it on demand.

    The function is the heart of the two-tier resolver. Seven
    strategy steps in order:

    1. **Depth cap.** A recursion depth above MAX_DEPTH means
       the snapshot has runaway cycles or the call site is
       misusing the helper. Return None and log a warning so
       the import does not lock up.
    2. **Cycle detection (FEAT-36b4).** If the key is already
       on `processing_stack`, the target is being created by
       an outer recursion frame; we cannot recurse into it
       again. Caller is expected to append a DeferredFK to
       the queue with its own child fields filled in; we
       return None so the outer FK lands as null on Phase-1.
    3. **Destination tier (FEAT-36b2).** Ask the destination
       NKIndex; on a hit, return the id immediately. No
       network, no snapshot lookup, no recursion.
    3a. **Failure short-circuit.** If a previous
       attempt to create this record failed AND step 3 did
       not find an id on the destination, return None
       immediately. Caching the failure makes
       second-and-later sibling references O(1) lookups
       instead of repeated failing HTTP round-trips.
       Optional, callers that omit `failed_keys` keep the
       legacy retry-every-time behaviour.
    4. **Snapshot tier (FEAT-36b3).** Ask the SnapshotIndex.
       On a miss, return None and let the caller drop the FK
       via the existing out-of-scope path.
    5. **Body resolution.** Push the key onto
       `processing_stack` and route the snapshot body through
       `_resolve_body` so every FK in the body is replaced
       with a resolved destination id. Required when `openapi`
       is provided; without that handle the function falls
       back to posting the raw body for backwards compatibility
       with older callers.
    6. **Recursive upsert (FEAT-36b3).** Call `upsert()` with
       the resolved body and pop the processing-stack key in a
       finally. The upsert populates the destination NKIndex
       with the new id automatically. On FAILED outcome the key
       is added to `failed_keys` (if provided) so subsequent
       attempts short-circuit via step 3a above.
    """

    # Runtime import of upsert so the module graph stays
    # acyclic: lookahead is consumed by driver.py, driver
    # consumes upsert.py; importing upsert at module top would
    # close the loop.
    from nbsnap.import_.upsert import UpsertOutcome, upsert

    key = (content_type, natural_key)

    # Step 1, depth cap.
    if depth >= MAX_DEPTH:
        logger.warning(
            "look-ahead depth %d hit at %s NK=%r; dropping",
            MAX_DEPTH, content_type, natural_key,
        )
        return None

    # Step 2, cycle detection. The caller pushes the queue
    # entry because only the caller knows the (child_ct,
    # child_nk, field_name) it was processing when the cycle
    # surfaced.
    if key in processing_stack:
        logger.debug("cycle at %s NK=%r, deferring to Phase-2",
                     content_type, natural_key)
        return None

    # Step 3, destination tier. Build the per-CT index lazily
    # (idempotent after the first call). The destination check
    # comes BEFORE the failure short-circuit so that if another
    # path managed to create the record after the original
    # failure (e.g. via the main Phase-1 phase later in the
    # plan), the subsequent reference picks up the new id
    # instead of believing the cached-failure verdict.
    dest_index.ensure_built(http, registry, content_type)
    existing = dest_index.lookup(content_type, natural_key)
    if existing is not None:
        return existing

    # Step 3a, failure short-circuit AFTER the destination
    # lookup. If a previous create attempt for this key
    # returned FAILED AND the record is still absent from the
    # destination, do not retry. Without this guard, every
    # child that references the same failed parent would
    # re-issue the same failing POST. The caller routes the
    # subsequent drop into the UPSERT_FAILED audit bucket so
    # the operator sees destination/policy issues
    # distinguished from missing-source data.
    if failed_keys is not None and key in failed_keys:
        logger.debug(
            "look-ahead skip %s NK=%r, previous attempt failed",
            content_type, natural_key,
        )
        return None

    # Step 4, snapshot tier.
    snapshot_body = snapshot_index.lookup(content_type, natural_key)
    if snapshot_body is None:
        return None  # out-of-scope or genuinely missing target

    # Step 5, recursive upsert. Push the key, then RESOLVE THE
    # BODY before upsert and pop the key in a finally so an
    # inner raise does not leave the stack corrupted.
    #
    # FEAT-36 follow-up: The naive `body=dict(snapshot_body)`
    # call sent raw NK-shaped FKs at NetBox. NetBox refuses with
    # HTTP 400 because, for example, `manufacturer: ["debian"]`
    # is not a valid integer FK. We now route the snapshot body
    # through the same `_resolve_body` the driver's main loop uses
    # so every FK in the body is replaced with the destination's
    # numeric id before the POST fires.
    #
    # `_resolve_body` lives in driver.py; importing it at module
    # top would close the cycle (lookahead -> driver -> upsert
    # -> lookahead). The runtime import here is the same pattern
    # we use for `upsert` above, and the cycle protection in
    # `processing_stack` survives the mutual recursion through
    # `_resolve_body`'s own `_try_lookahead` callout.
    processing_stack.add(key)
    try:
        if openapi is not None:
            from nbsnap.import_.driver import _resolve_body

            resolved_body = _resolve_body(
                content_type,
                dict(snapshot_body),
                openapi,
                dest_index,
                http,
                registry,
                snapshot_index=snapshot_index,
                processing_stack=processing_stack,
                deferred_queue=deferred_queue,
                current_nk=natural_key,
                auditor=auditor,
                failed_keys=failed_keys,
                deferred_fields_by_ct=deferred_fields_by_ct,
            )
        else:
            # Backwards-compat for callers that have not yet
            # threaded the openapi handle through. The unresolved
            # body still flows, matching pre-fix behaviour, so the
            # existing unit tests keep passing while the driver
            # call sites get updated.
            resolved_body = dict(snapshot_body)

        result = upsert(
            http,
            content_type=content_type,
            natural_key=natural_key,
            body=resolved_body,
            index=dest_index,
            registry=registry,
        )
    finally:
        processing_stack.discard(key)

    if result.outcome is UpsertOutcome.FAILED:
        # cache the failure so siblings that reference
        # the same parent do not re-issue the same failing POST.
        if failed_keys is not None:
            failed_keys.add(key)
        logger.warning(
            "look-ahead upsert failed for %s NK=%r: %s",
            content_type, natural_key, result.message,
        )
        return None
    return result.destination_id
