"""Shared state container for the import-side FK resolver.

The resolver call graph in `driver.py` and `lookahead.py`
threads 10+ kwargs through five call sites:

* `http`, `index`, `registry` — destination NetBox handles.
* `snapshot_index`, `processing_stack`, `deferred_queue` —
  look-ahead state.
* `auditor`, `failed_keys`, `transient_keys`,
  `deferred_fields_by_ct`, `warn_dedup` — audit and
  classification state.
* `openapi` — schema handle.
* `current_nk` — added in ARCH-02b. The NK of the record
  currently being resolved, used for cycle detection and for
  enriching :class:`ResolverFKMissError` with child context.

`ResolveContext` collects these into a single frozen
dataclass so a caller can build it once at the top of
`run_import` and pass it down. REFACTOR-01a lands only the
dataclass; the migration of `_try_lookahead` / `resolve_or_create`
to consume it lives in REFACTOR-01b, and the pre-passes
in REFACTOR-01c.

ARCH-02b adds :meth:`ResolveContext.fresh`, a classmethod that
builds a fully-initialised context with empty mutable defaults
for tests. ARCH-02c migrates ``_resolve_body`` to consume
:class:`ResolveContext` directly instead of the nine-keyword
bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid runtime cycles
    from nbsnap.http.client import NetboxHTTP
    from nbsnap.import_.audit import Auditor
    from nbsnap.import_.nk_index import NKIndex
    from nbsnap.import_.snapshot_index import SnapshotIndex
    from nbsnap.natkey.model import NKRegistry
    from nbsnap.schema.openapi import OpenAPI


@dataclass(frozen=True)
class ResolveContext:
    """Bundle of FK-resolver state shared across the call graph.

    The dataclass is ``frozen`` so a reference passed down the call
    graph cannot accidentally be re-bound to a different bundle.
    The mutable contents (sets, lists, dicts) are still mutable in
    place; the resolver mutates ``processing_stack`` and the audit
    accumulators directly. Frozen-on-the-outside, mutable-inside is
    the intended shape.
    """

    http: NetboxHTTP
    index: NKIndex
    registry: NKRegistry
    openapi: OpenAPI
    snapshot_index: SnapshotIndex
    processing_stack: set[tuple[str, tuple[Any, ...]]]
    deferred_queue: list[Any]
    auditor: Auditor | None
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None
    transient_keys: set[tuple[str, tuple[Any, ...]]] | None
    deferred_fields_by_ct: dict[str, set[str]] | None
    warn_dedup: set[tuple[str, str, str]] | None
    # ARCH-02b: the NK of the record currently being resolved. Set
    # by the driver before each top-level _resolve_body call so the
    # leaf raise sites can enrich ResolverFKMissError with child
    # context (the leaf otherwise only knows the parent NK).
    current_nk: tuple[Any, ...] = ()

    @classmethod
    def fresh(
        cls,
        http: NetboxHTTP | None = None,
        index: NKIndex | None = None,
        registry: NKRegistry | None = None,
        openapi: OpenAPI | None = None,
        snapshot_index: SnapshotIndex | None = None,
    ) -> ResolveContext:
        """Build a context with empty mutable defaults for tests.

        Production callers (``run_import``) construct the context
        explicitly with all eight backing handles. Tests usually
        want a context whose mutable accumulators (processing_stack,
        deferred_queue, audit sets) start empty so they can drive a
        single resolver call and inspect what landed.

        The HTTP, index, registry, openapi, and snapshot_index
        kwargs default to ``None`` and the caller injects a mock,
        most tests do not actually reach the destination; the type
        hint is "any" of those for the test bench, the production
        path always supplies a real handle.
        """

        return cls(
            http=http,  # type: ignore[arg-type]
            index=index,  # type: ignore[arg-type]
            registry=registry,  # type: ignore[arg-type]
            openapi=openapi,  # type: ignore[arg-type]
            snapshot_index=snapshot_index,  # type: ignore[arg-type]
            processing_stack=set(),
            deferred_queue=[],
            auditor=None,
            failed_keys=set(),
            transient_keys=set(),
            deferred_fields_by_ct={},
            warn_dedup=set(),
            current_nk=(),
        )
