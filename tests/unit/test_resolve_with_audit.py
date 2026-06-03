"""REFACTOR-02 tests for the unified resolve-with-audit helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nbsnap.import_.audit import Auditor, DropCategory
from nbsnap.import_.driver import resolve_with_audit
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.resolve_context import ResolveContext
from nbsnap.import_.snapshot_index import SnapshotIndex


def _ctx(**overrides) -> ResolveContext:
    """Build a default ResolveContext suitable for tests."""

    defaults: dict = {
        "http": MagicMock(),
        "index": NKIndex(),
        "registry": MagicMock(),
        "openapi": MagicMock(),
        "snapshot_index": SnapshotIndex(),
        "processing_stack": set(),
        "deferred_queue": [],
        "auditor": Auditor(),
        "failed_keys": set(),
        "transient_keys": set(),
        "deferred_fields_by_ct": {},
        "warn_dedup": set(),
    }
    defaults.update(overrides)
    return ResolveContext(**defaults)


def test_helper_returns_rid_on_lookahead_hit() -> None:
    """When _try_lookahead resolves the FK, the helper returns
    `(rid, None)` and the auditor records nothing."""

    ctx = _ctx()
    with patch(
        "nbsnap.import_.driver._try_lookahead",
        return_value=(42, False),
    ):
        rid, category = resolve_with_audit(
            ctx=ctx,
            value=["x"],
            target_ct="dcim.site",
            child_ct="dcim.device",
            child_nk=("d",),
            field_name="site",
        )
    assert rid == 42
    assert category is None
    assert ctx.auditor.events == []


def test_helper_records_drop_when_lookahead_misses() -> None:
    """When _try_lookahead returns (None, False), the helper
    routes through _record_drop and emits the audit event,
    returning `(None, category)`."""

    snap = SnapshotIndex()
    snap._by_key[("dcim.site", ("other",))] = {}
    ctx = _ctx(snapshot_index=snap)
    with patch(
        "nbsnap.import_.driver._try_lookahead",
        return_value=(None, False),
    ):
        rid, category = resolve_with_audit(
            ctx=ctx,
            value=["ghost"],
            target_ct="dcim.site",
            child_ct="dcim.device",
            child_nk=("d",),
            field_name="site",
        )
    assert rid is None
    assert category is DropCategory.MISSING_FROM_SOURCE
    assert len(ctx.auditor.events) == 1


def test_helper_reports_deferred_category() -> None:
    """When _try_lookahead deferred the field onto Phase-2,
    the helper surfaces DEFERRED_TO_PHASE2 in the return."""

    ctx = _ctx()
    with patch(
        "nbsnap.import_.driver._try_lookahead",
        return_value=(None, True),
    ):
        rid, category = resolve_with_audit(
            ctx=ctx,
            value=["x"],
            target_ct="ipam.ipaddress",
            child_ct="dcim.device",
            child_nk=(("h",), "d"),
            field_name="primary_ip4",
        )
    assert rid is None
    assert category is DropCategory.DEFERRED_TO_PHASE2
