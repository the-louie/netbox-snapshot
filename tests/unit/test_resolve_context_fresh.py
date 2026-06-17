"""ARCH-02b: :meth:`ResolveContext.fresh` returns a fully-initialised bundle.

Pins three things:

* The classmethod builds a context with all the mutable
  accumulators non-None and empty.
* ``current_nk`` defaults to ``()``.
* Mutating the returned accumulators is allowed even though the
  dataclass is frozen, because frozen-on-the-outside-mutable-inside
  is the documented shape.
"""

from __future__ import annotations

import pytest

from nbsnap.import_.resolve_context import ResolveContext


def test_fresh_returns_initialised_mutable_state() -> None:
    ctx = ResolveContext.fresh()

    assert ctx.processing_stack == set()
    assert ctx.deferred_queue == []
    assert ctx.failed_keys == set()
    assert ctx.transient_keys == set()
    assert ctx.deferred_fields_by_ct == {}
    assert ctx.warn_dedup == set()
    assert ctx.auditor is None
    assert ctx.current_nk == ()


def test_fresh_mutable_fields_are_mutable_in_place() -> None:
    """The frozen dataclass does not stop mutation of contained mutables.

    The resolver call graph relies on this: ``processing_stack.add(...)``
    and ``deferred_queue.append(...)`` must work even though the dataclass
    is frozen so the bundle reference cannot be re-bound.
    """

    ctx = ResolveContext.fresh()
    ctx.processing_stack.add(("dcim.device", ("d39a",)))
    ctx.deferred_queue.append("anything")

    assert len(ctx.processing_stack) == 1
    assert ctx.deferred_queue == ["anything"]


def test_fresh_bundle_reference_is_immutable() -> None:
    """Reassigning a field on the returned context raises.

    The point of ``frozen=True`` is to catch a caller that tries to
    swap ``ctx.http`` for a different client mid-run; that would
    confuse anyone reading the call graph.
    """

    ctx = ResolveContext.fresh()
    with pytest.raises(Exception):
        # dataclasses.FrozenInstanceError inherits AttributeError in
        # 3.11; pytest.raises(Exception) is broad enough.
        ctx.current_nk = ("nope",)  # type: ignore[misc]
