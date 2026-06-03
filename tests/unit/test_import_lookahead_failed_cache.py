"""Tests for task #29: failed-lookahead cache.

When `resolve_or_create` POSTs a record that NetBox refuses,
the result is `FAILED`. Without a cache, every subsequent
child that references the same parent re-invokes
`resolve_or_create` and tries the same failing POST again.

In the rescue-10 rerun this turned a single bad Device into
dozens of identical HTTP 400 lines on stderr, one per
Interface that referenced it. The cache turns
second-and-later attempts into O(1) lookups.

Three behaviours pinned here:

1. After a FAILED upsert, the `(content_type, NK)` key lands
   in the `failed_keys` set.
2. A second `resolve_or_create` call for the same key
   short-circuits at the new step 2 and returns `None`
   without touching http.post.
3. When `failed_keys` is not passed (legacy callers), the
   retry-every-time behaviour survives so existing tests
   that do not wire the cache keep passing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.lookahead import resolve_or_create
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.natkey.registry import default as default_registry


def _fixture():
    """A snapshot with one record that we will fail to POST."""

    snapshot_index = SnapshotIndex()
    snapshot_index._by_key[("dcim.site", ("hall-d",))] = {
        "name": "Hall-D", "slug": "hall-d",
    }
    http = MagicMock()
    http.get_all.return_value = iter([])
    # Simulate NetBox refusing the POST. The upsert path catches
    # the exception and returns UpsertOutcome.FAILED.
    http.post.side_effect = RuntimeError("boom")
    return {
        "http": http,
        "snapshot_index": snapshot_index,
        "dest_index": NKIndex(),
        "registry": default_registry(),
    }


def test_first_failed_attempt_populates_failed_keys() -> None:
    """After a FAILED upsert, the key sits in `failed_keys` so
    a follow-up call can short-circuit."""

    state = _fixture()
    failed_keys: set = set()

    rid = resolve_or_create(
        state["http"], state["snapshot_index"],
        state["dest_index"], state["registry"],
        content_type="dcim.site",
        natural_key=("hall-d",),
        processing_stack=set(),
        deferred_queue=[],
        failed_keys=failed_keys,
    )
    assert rid is None
    # The key was added to the cache.
    assert ("dcim.site", ("hall-d",)) in failed_keys
    # And the http.post was called exactly once.
    assert state["http"].post.call_count == 1


def test_second_attempt_short_circuits_via_failed_keys() -> None:
    """When the key is already in `failed_keys`, the second
    call returns None immediately without touching http.post."""

    state = _fixture()
    failed_keys = {("dcim.site", ("hall-d",))}

    rid = resolve_or_create(
        state["http"], state["snapshot_index"],
        state["dest_index"], state["registry"],
        content_type="dcim.site",
        natural_key=("hall-d",),
        processing_stack=set(),
        deferred_queue=[],
        failed_keys=failed_keys,
    )
    assert rid is None
    # http.post was NOT called because of the early short-circuit.
    state["http"].post.assert_not_called()


def test_legacy_caller_without_failed_keys_keeps_retrying() -> None:
    """Backwards-compat: a caller that does not pass
    `failed_keys` gets the pre-fix behaviour (retry every
    time). Existing unit tests that do not wire the cache
    keep working."""

    state = _fixture()

    # First call: fails.
    resolve_or_create(
        state["http"], state["snapshot_index"],
        state["dest_index"], state["registry"],
        content_type="dcim.site",
        natural_key=("hall-d",),
        processing_stack=set(),
        deferred_queue=[],
    )
    # Second call WITHOUT failed_keys: fails again, http.post
    # called a second time.
    resolve_or_create(
        state["http"], state["snapshot_index"],
        state["dest_index"], state["registry"],
        content_type="dcim.site",
        natural_key=("hall-d",),
        processing_stack=set(),
        deferred_queue=[],
    )
    assert state["http"].post.call_count == 2


def test_successful_upsert_does_not_populate_failed_keys() -> None:
    """A successful create leaves `failed_keys` empty so a
    later re-resolution (e.g. for the destination-tier cache
    hit branch) still works."""

    snapshot_index = SnapshotIndex()
    snapshot_index._by_key[("dcim.site", ("hall-d",))] = {
        "name": "Hall-D", "slug": "hall-d",
    }
    http = MagicMock()
    http.get_all.return_value = iter([])
    http.post.return_value = {"id": 42}

    failed_keys: set = set()
    rid = resolve_or_create(
        http, snapshot_index, NKIndex(), default_registry(),
        content_type="dcim.site",
        natural_key=("hall-d",),
        processing_stack=set(),
        deferred_queue=[],
        failed_keys=failed_keys,
    )
    assert rid == 42
    # No failure recorded.
    assert ("dcim.site", ("hall-d",)) not in failed_keys


def test_destination_lookup_runs_before_failed_keys_short_circuit() -> None:
    """The short-circuit is step 3a, AFTER the destination
    tier (step 3). If the destination has the record (e.g.
    because another path managed to create it after the
    original failure), the existing id wins. The failure
    cache only suppresses retries when the record is STILL
    missing from the destination."""

    dest = NKIndex()
    # Destination has id=99 for this key. Even though we cached
    # a previous failure, the existing id wins because the
    # destination check runs first.
    dest.insert("dcim.site", ("hall-d",), 99)

    failed_keys = {("dcim.site", ("hall-d",))}
    http = MagicMock()
    http.get_all.return_value = iter([])

    rid = resolve_or_create(
        http, SnapshotIndex(), dest, default_registry(),
        content_type="dcim.site",
        natural_key=("hall-d",),
        processing_stack=set(),
        deferred_queue=[],
        failed_keys=failed_keys,
    )
    # The destination lookup wins, return the live id 99.
    assert rid == 99


def test_transient_5xx_failure_does_not_get_cached() -> None:
    """FEAT-45a: a 503 response leaves the key OUT of
    `failed_keys`. The next look-ahead retries instead of
    dropping the FK with a phantom MISSING_FROM_SOURCE."""

    from unittest.mock import MagicMock, patch
    from nbsnap.import_.lookahead import resolve_or_create
    from nbsnap.import_.nk_index import NKIndex
    from nbsnap.import_.snapshot_index import SnapshotIndex
    from nbsnap.import_.upsert import UpsertOutcome, UpsertResult

    failed_keys: set = set()
    fake_result = UpsertResult(
        outcome=UpsertOutcome.FAILED,
        content_type="dcim.site",
        natural_key=("hall-a",),
        destination_id=None,
        message="POST failed: 503",
        http_status=503,
    )
    snapshot_index = SnapshotIndex()
    snapshot_index._by_key[("dcim.site", ("hall-a",))] = {"slug": "a"}

    with patch("nbsnap.import_.upsert.upsert", return_value=fake_result):
        resolve_or_create(
            MagicMock(get_all=MagicMock(return_value=iter([]))),
            snapshot_index,
            NKIndex(),
            MagicMock(),
            content_type="dcim.site",
            natural_key=("hall-a",),
            processing_stack=set(),
            deferred_queue=[],
            failed_keys=failed_keys,
        )
    assert ("dcim.site", ("hall-a",)) not in failed_keys


def test_permanent_4xx_failure_is_cached() -> None:
    """FEAT-45a: a 400 response stays cached so subsequent
    look-aheads short-circuit instead of re-issuing the same
    failing POST."""

    from unittest.mock import MagicMock, patch
    from nbsnap.import_.lookahead import resolve_or_create
    from nbsnap.import_.nk_index import NKIndex
    from nbsnap.import_.snapshot_index import SnapshotIndex
    from nbsnap.import_.upsert import UpsertOutcome, UpsertResult

    failed_keys: set = set()
    fake_result = UpsertResult(
        outcome=UpsertOutcome.FAILED,
        content_type="dcim.site",
        natural_key=("hall-a",),
        destination_id=None,
        message="POST failed: 400",
        http_status=400,
    )
    snapshot_index = SnapshotIndex()
    snapshot_index._by_key[("dcim.site", ("hall-a",))] = {"slug": "a"}

    with patch("nbsnap.import_.upsert.upsert", return_value=fake_result):
        resolve_or_create(
            MagicMock(get_all=MagicMock(return_value=iter([]))),
            snapshot_index,
            NKIndex(),
            MagicMock(),
            content_type="dcim.site",
            natural_key=("hall-a",),
            processing_stack=set(),
            deferred_queue=[],
            failed_keys=failed_keys,
        )
    assert ("dcim.site", ("hall-a",)) in failed_keys
