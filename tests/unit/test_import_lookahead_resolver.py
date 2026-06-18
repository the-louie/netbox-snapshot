"""FEAT-36b2..b4 tests for `resolve_or_create`.

Three behaviours, all in one file because the helper is a
short function and the call sites differ only by which
fixture state they pre-populate.

* Destination tier (FEAT-36b2): destination NKIndex has the
  target, we return its id without consulting the snapshot.
* Snapshot tier (FEAT-36b3): destination misses, snapshot has
  the target, we recursively upsert and return the new id.
  Snapshot misses too, we return None.
* Cycle detection (FEAT-36b4): the key is on the processing
  stack, we return None without recursing, the caller is
  responsible for appending the DeferredFK.
* Depth cap, MAX_DEPTH is honoured.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nbsnap.import_.lookahead import MAX_DEPTH, DeferredFK, resolve_or_create
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.natkey.registry import default as default_registry


@pytest.fixture()
def empty_state() -> dict:
    """A fresh set of resolver state objects."""

    http = MagicMock()
    http.get_all.return_value = iter([])  # destination is empty
    return {
        "http": http,
        "snapshot_index": SnapshotIndex(),
        "dest_index": NKIndex(),
        "registry": default_registry(),
        "processing_stack": set(),
        "deferred_queue": [],
    }


# ---------------------------------------------------------------------------
# FEAT-36b2: destination-tier hit
# ---------------------------------------------------------------------------


def test_destination_hit_returns_id_without_snapshot_lookup(empty_state) -> None:
    """If the destination already has the target, return its id
    immediately and do not touch the snapshot index."""

    empty_state["dest_index"].insert("dcim.site", ("hall-d",), 42)

    rid = resolve_or_create(
        content_type="dcim.site",
        natural_key=("hall-d",),
        **empty_state,
    )
    assert rid == 42
    # No POST fired against destination.
    empty_state["http"].post.assert_not_called()


# ---------------------------------------------------------------------------
# FEAT-36b3: snapshot tier
# ---------------------------------------------------------------------------


def test_snapshot_tier_creates_target_on_demand(empty_state) -> None:
    """Destination misses, snapshot has the target, the
    resolver recursively upserts and returns the new id."""

    # Pre-seed the snapshot.
    empty_state["snapshot_index"]._by_key[("dcim.site", ("hall-d",))] = {
        "name": "Hall-D",
        "slug": "hall-d",
    }
    # The upsert path calls http.post; stub it to return a
    # NetBox-shaped response with the new id.
    empty_state["http"].post.return_value = {"id": 99}

    rid = resolve_or_create(
        content_type="dcim.site",
        natural_key=("hall-d",),
        **empty_state,
    )
    assert rid == 99
    # The destination index now carries the new id, so a
    # subsequent lookup hits the destination tier directly.
    assert empty_state["dest_index"].lookup("dcim.site", ("hall-d",)) == 99


def test_snapshot_miss_returns_none(empty_state) -> None:
    """Both indexes miss, return None so the caller can drop
    the FK via the existing out-of-scope path."""

    rid = resolve_or_create(
        content_type="dcim.region",  # nothing seeded
        natural_key=("elmia",),
        **empty_state,
    )
    assert rid is None
    empty_state["http"].post.assert_not_called()


# ---------------------------------------------------------------------------
# FEAT-36b4: cycle detection
# ---------------------------------------------------------------------------


def test_cycle_on_stack_returns_none_without_recursion(empty_state) -> None:
    """If the key is on the processing stack, we are mid-recursion
    into this very record; return None so the caller can defer
    the FK to Phase-2."""

    # Pretend we are already creating dcim.device d39a higher up
    # the call chain. A recursive call into the same key returns
    # None without touching upsert.
    empty_state["processing_stack"].add(("dcim.device", (("hall-d",), "d39a")))
    # Even though the snapshot has the record, the cycle bites
    # first.
    empty_state["snapshot_index"]._by_key[("dcim.device", (("hall-d",), "d39a"))] = {
        "name": "d39a",
    }

    rid = resolve_or_create(
        content_type="dcim.device",
        natural_key=(("hall-d",), "d39a"),
        **empty_state,
    )
    assert rid is None
    empty_state["http"].post.assert_not_called()


def test_processing_stack_cleaned_up_on_success(empty_state) -> None:
    """After a successful recursive upsert, the key is removed
    from the processing stack so siblings can be processed."""

    empty_state["snapshot_index"]._by_key[("dcim.site", ("hall-d",))] = {
        "name": "Hall-D",
    }
    empty_state["http"].post.return_value = {"id": 5}

    pre_size = len(empty_state["processing_stack"])
    resolve_or_create(
        content_type="dcim.site",
        natural_key=("hall-d",),
        **empty_state,
    )
    assert len(empty_state["processing_stack"]) == pre_size


def test_processing_stack_cleaned_up_on_upsert_failure(empty_state) -> None:
    """When upsert returns FAILED, the resolver still pops the
    key off the processing stack so subsequent siblings can
    continue. `upsert` catches POST errors internally (returns
    UpsertOutcome.FAILED) rather than re-raising, so we drive
    the failure path via a falsy created-id rather than an
    exception."""

    empty_state["snapshot_index"]._by_key[("dcim.site", ("hall-d",))] = {
        "name": "Hall-D",
    }
    empty_state["http"].post.side_effect = RuntimeError("boom")

    rid = resolve_or_create(
        content_type="dcim.site",
        natural_key=("hall-d",),
        **empty_state,
    )
    # The resolver swallowed the inner failure via upsert's
    # own try/except and returned None.
    assert rid is None
    # The cleanup ran regardless.
    assert ("dcim.site", ("hall-d",)) not in empty_state["processing_stack"]


# ---------------------------------------------------------------------------
# Depth cap (MAX_DEPTH)
# ---------------------------------------------------------------------------


def test_depth_cap_returns_none(empty_state) -> None:
    """At MAX_DEPTH the helper returns None and does not
    recurse further."""

    rid = resolve_or_create(
        content_type="dcim.site",
        natural_key=("hall-d",),
        depth=MAX_DEPTH,
        **empty_state,
    )
    assert rid is None


def test_deferred_fk_can_be_constructed_and_appended_to_queue() -> None:
    """The caller is responsible for pushing DeferredFK
    entries; verify the queue accepts the type. Smoke test
    for the data shape that FEAT-36b5 wires in."""

    queue: list[DeferredFK] = []
    entry = DeferredFK(
        child_content_type="dcim.device",
        child_nk=(("hall-d",), "d39a"),
        field_name="primary_ip4",
        target_content_type="ipam.ipaddress",
        target_nk=("172.16.1.10/24",),
    )
    queue.append(entry)
    assert queue == [entry]
