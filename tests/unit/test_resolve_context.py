"""REFACTOR-01a smoke tests for `ResolveContext`."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_resolve_context_holds_all_fields() -> None:
    """Build a ResolveContext from mocks and assert every field
    is accessible."""

    from nbsnap.import_.resolve_context import ResolveContext

    http = MagicMock()
    index = MagicMock()
    registry = MagicMock()
    openapi = MagicMock()
    snapshot_index = MagicMock()
    processing_stack: set = set()
    deferred_queue: list = []
    auditor = MagicMock()
    failed_keys: set = set()
    transient_keys: set = set()
    deferred_fields_by_ct: dict = {}
    warn_dedup: set = set()

    ctx = ResolveContext(
        http=http,
        index=index,
        registry=registry,
        openapi=openapi,
        snapshot_index=snapshot_index,
        processing_stack=processing_stack,
        deferred_queue=deferred_queue,
        auditor=auditor,
        failed_keys=failed_keys,
        transient_keys=transient_keys,
        deferred_fields_by_ct=deferred_fields_by_ct,
        warn_dedup=warn_dedup,
    )
    assert ctx.http is http
    assert ctx.index is index
    assert ctx.registry is registry
    assert ctx.openapi is openapi
    assert ctx.snapshot_index is snapshot_index
    assert ctx.processing_stack is processing_stack
    assert ctx.deferred_queue is deferred_queue
    assert ctx.auditor is auditor
    assert ctx.failed_keys is failed_keys
    assert ctx.transient_keys is transient_keys
    assert ctx.deferred_fields_by_ct is deferred_fields_by_ct
    assert ctx.warn_dedup is warn_dedup


def test_resolve_context_exported_from_package() -> None:
    """`from nbsnap.import_ import ResolveContext` works."""

    from nbsnap.import_ import ResolveContext  # noqa: F401
