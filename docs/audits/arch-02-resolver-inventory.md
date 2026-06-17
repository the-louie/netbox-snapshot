# ARCH-02a inventory: `_resolve_body` call sites and state bundle

This document is the input to ARCH-02b/c, the migration of
`_resolve_body` from its current nine-keyword-only-state-fields
signature to `_resolve_body(content_type, body, ctx)`.

## Signature today (`src/nbsnap/import_/driver.py:419-436`)

```python
def _resolve_body(
    content_type: str,
    body: dict[str, Any],
    openapi: OpenAPI,
    index: NKIndex,
    http: NetboxHTTP,
    registry: Any,
    *,
    snapshot_index: _SnapshotIndexType | None = None,
    processing_stack: set[tuple[str, tuple[Any, ...]]] | None = None,
    deferred_queue: list[Any] | None = None,
    current_nk: tuple[Any, ...] = (),
    auditor: Auditor | None = None,
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
    deferred_fields_by_ct: dict[str, set[str]] | None = None,
    warn_dedup: set[tuple[str, str, str]] | None = None,
    transient_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
) -> dict[str, Any]:
```

The six leading positional params plus the nine keyword-only state
fields are the "wide" surface ARCH-02b/c contracts.

## Direct callers

| File:line | Notes |
| :--- | :--- |
| `src/nbsnap/import_/driver.py:280` | Inside the Phase-1 loop. Passes every state bundle from the surrounding closure. |
| `src/nbsnap/import_/lookahead.py:258` | Inside the cycle-resolution recursion. Re-packages the same state bundle from the lookahead caller. |

Both call sites rebuild the same nine-field bundle. ARCH-02b's
`ResolveContext` is the single object that replaces both
rebuilds.

## Existing `ResolveContext` (`src/nbsnap/import_/resolve_context.py`)

The dataclass already carries `http`, `openapi`, `nk_index`,
`registry`, `cancel_event`, `dry_run`, `endpoint_index`,
`deferred_fields_by_ct`, `processing_stack`. ARCH-02b adds the
**delta**:

* `snapshot_index`
* `deferred_queue`
* `current_nk`
* `auditor`
* `failed_keys`
* `warn_dedup`
* `transient_keys`

…plus a `ResolveContext.fresh()` classmethod that constructs a
fully-initialised context for tests.

## Tests that construct the bundle today

* `tests/unit/test_import_driver_lookahead.py`
* `tests/unit/test_import_lookahead_body_resolution.py`
* `tests/unit/test_import_lookahead_failed_cache.py`
* `tests/unit/test_resolve_with_audit.py`
* `tests/unit/test_resolve_context.py`

The first three pass the full keyword bundle and would shrink to
a `ctx = ResolveContext.fresh(); _resolve_body(ct, body, ctx)`
pattern once ARCH-02c lands.

## Migration order

1. **ARCH-02b** (this audit's prerequisite): extend `ResolveContext`
   with the delta fields plus `fresh()`. Pure dataclass work, no
   call-site change yet.
2. **ARCH-02c**: rewrite `_resolve_body` signature and the
   driver-internal call sites.
3. **ARCH-02d**: migrate `lookahead.py:258`.

ARCH-02e..i sit on top: Phase-1 / Phase-2 runner extractions,
field-resolver helpers, and the slim-down of `driver.py`.

## Why an inventory step

`_resolve_body` is the single hottest function in `import_/`,
touched by every record. ARCH-01 (just shipped) proved that
methodical sub-tickets with a shared inventory survive context
switches; without this document the migration would re-discover
the call-graph each session.
