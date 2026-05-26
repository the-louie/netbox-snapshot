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

# NaturalKey is the shared tuple-of-tuples alias used by every
# NK consumer. It lives in snapshot_index because the loader is
# the first module that needs to type it; treat that as the
# canonical home.
from nbsnap.import_.snapshot_index import NaturalKey

__all__ = ["DeferredFK", "MAX_DEPTH"]

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
