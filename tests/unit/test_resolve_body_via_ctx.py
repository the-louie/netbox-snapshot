"""ARCH-02c: ``_resolve_body_via_ctx`` thin wrapper.

The wrapper exposes the 3-arg ``(content_type, body, ctx)``
signature the audit asks for, delegating to the legacy
:func:`_resolve_body` underneath. ARCH-02h will inline the body
once the driver is slimmed further; until then this wrapper is
the migration anchor that lets every caller adopt the typed
context without a 200-line rewrite.

The test patches ``_resolve_body`` and asserts the wrapper
unpacks the :class:`ResolveContext` into the right legacy
keyword bundle. We do not run the real resolver because its
internals require a real OpenAPI schema.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nbsnap.import_.resolve_context import ResolveContext


def _fresh_ctx() -> ResolveContext:
    """Build a context with mocks for every backing handle."""

    return ResolveContext.fresh(
        http=MagicMock(),
        index=MagicMock(),
        registry=MagicMock(),
        openapi=MagicMock(),
        snapshot_index=MagicMock(),
    )


def test_via_ctx_unpacks_into_legacy_kwargs() -> None:
    """The wrapper hands the legacy ``_resolve_body`` every state field
    sourced from the context.

    This locks the un-packing shape so a future refactor that
    drops one of the context fields trips here loudly instead of
    quietly producing a half-populated kwargs bundle.
    """

    ctx = _fresh_ctx()

    with patch("nbsnap.import_.driver._resolve_body") as fake:
        fake.return_value = {"ok": True}
        from nbsnap.import_.driver import _resolve_body_via_ctx

        out = _resolve_body_via_ctx("dcim.site", {"name": "x"}, ctx)

    assert out == {"ok": True}
    args, kwargs = fake.call_args
    # Positional args mirror the legacy contract:
    # (content_type, body, openapi, index, http, registry)
    assert args[0] == "dcim.site"
    assert args[1] == {"name": "x"}
    assert args[2] is ctx.openapi
    assert args[3] is ctx.index
    assert args[4] is ctx.http
    assert args[5] is ctx.registry

    # Every legacy keyword the audit's inventory lists is sourced
    # from the context. The set must match exactly.
    expected_kwargs = {
        "snapshot_index",
        "processing_stack",
        "deferred_queue",
        "current_nk",
        "auditor",
        "failed_keys",
        "deferred_fields_by_ct",
        "warn_dedup",
        "transient_keys",
    }
    assert set(kwargs) == expected_kwargs


def test_via_ctx_returns_what_resolve_body_returns() -> None:
    """A simple sanity sentinel-pass through, nothing fancier."""

    ctx = _fresh_ctx()
    sentinel = {"sentinel": True}

    with patch("nbsnap.import_.driver._resolve_body", return_value=sentinel):
        from nbsnap.import_.driver import _resolve_body_via_ctx

        assert _resolve_body_via_ctx("dcim.site", {}, ctx) is sentinel
