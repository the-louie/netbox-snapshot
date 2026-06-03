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

`ResolveContext` collects these into a single frozen
dataclass so a caller can build it once at the top of
`run_import` and pass it down. REFACTOR-01a lands only the
dataclass; the migration of `_try_lookahead` / `resolve_or_create`
to consume it lives in REFACTOR-01b, and the pre-passes
in REFACTOR-01c.
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
    """Bundle of FK-resolver state shared across the call graph."""

    http: "NetboxHTTP"
    index: "NKIndex"
    registry: "NKRegistry"
    openapi: "OpenAPI"
    snapshot_index: "SnapshotIndex"
    processing_stack: set[tuple[str, tuple[Any, ...]]]
    deferred_queue: list[Any]
    auditor: "Auditor | None"
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None
    transient_keys: set[tuple[str, tuple[Any, ...]]] | None
    deferred_fields_by_ct: dict[str, set[str]] | None
    warn_dedup: set[tuple[str, str, str]] | None
