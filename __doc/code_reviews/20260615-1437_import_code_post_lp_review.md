# Import-side code review, post-loop landings (#18–#34)

## Summary

This review covers the import-side codebase after the latest round of bug fixes (#18 through #34). The code is functionally rich and shows clear evolution through many shipped fixes (cycle handling, look-ahead, audit categorisation, custom-fields filtering, POST-failure classification). Logic is generally correct, but the codebase has accumulated tactical debt around argument plumbing. `driver.py` carries a 10-plus parameter context that gets reassembled by hand at three `_try_lookahead` call sites and two pre-passes. Several real bugs lurk: an OUT_OF_SCOPE misclassification under transient HTTP failure, missing audit events for stripped deferred fields and M2M drops, and a substring classifier that lacks anchoring. Comments are mostly good (genuine why-comments) with a few that lean on operator slang or describe what changed rather than why.

The recommended order is: ship the correctness fixes immediately (Critical / High), schedule the DRY refactors as a follow-up (`ResolveContext`), and document the lower-impact items in `TODO.md` for backlog scheduling.

## Critical Findings

| Issue | Severity | File:Line | Description | Suggested Fix |
|---|---|---|---|---|
| OUT_OF_SCOPE misclassification under transient HTTP errors | **High** | driver.py: `_record_drop`, `_resolve_body` simple-FK catch | `resolve_simple_fk` can raise `ValueError` for reasons other than "not found" (e.g. HTTP error during `index.ensure_built`). The current catch is `except (KeyError, ValueError)` and both route through `_record_drop`. When the snapshot also lacks the CT (likely if `ensure_built` failed), the drop bucketsas OUT_OF_SCOPE and the warning is suppressed. Real failures vanish silently. | Distinguish KeyError (true miss) from ValueError. Only KeyError should ever route to OUT_OF_SCOPE; ValueError should warn-and-drop without audit muting. |
| Missing audit event for planner-deferred fields stripped from POST bodies | **High** | driver.py: `_strip_deferred_fields_and_queue` | When a field listed in `deferred_fields_by_ct` is stripped and queued, the function pushes a `DeferredFK` onto `deferred_queue` but does NOT call `auditor.record(DropEvent(DEFERRED_TO_PHASE2, ...))`. The audit summary undercounts deferrals: only those routed via `_try_lookahead`'s queue-size delta get audited. | Emit a `DropEvent` with `DEFERRED_TO_PHASE2` category alongside the `DeferredFK.append`. |
| M2M dropped items are not audited | **High** | driver.py: `_safe_resolve_m2m` | M2M items that fail FK resolution are dropped via `_warn_dropped` but no `DropEvent` lands. The categorised audit log misses them, so operators cannot see which `tags`, `tagged_vlans`, etc. references were lost. | Thread the auditor + drop classification into `_safe_resolve_m2m`. |
| Failure-cache classification asymmetry | **High** | lookahead.py `failed_keys.add`, driver.py `_record_drop` | A FAILED upsert through look-ahead caches the key. Subsequent sibling refs hit the 3a short-circuit, return None, and `_record_drop` computes `deferred_grew=False`, `has_content_type=True`, then buckets as MISSING_FROM_SOURCE. So the first failure is silent (only `logger.warning`) and siblings get audited under a misleading category. | Add a new DropCategory.UPSERT_FAILED (or LOOKAHEAD_FAILED), emit it on cache insertion AND when callers find a key in the cache. |
| Substring classifier in `_classify_post_failure` can false-positive | Medium | upsert.py:`_POST_FAILURE_SKIP_PATTERNS` + `_classify_post_failure` | "Defined addresses overlap" is matched anywhere in the error body. A future NetBox version or unrelated `__all__` payload quoting that phrase would silently flip real failures to SKIPPED. There is no anchoring. | Match on the structural NetBox error shape (`__all__` array containing the phrase) rather than a free-text substring. Or anchor the substring with surrounding tokens (e.g. "addresses overlap with range"). |
| Termination malformed-item skip is silent | Medium | driver.py:`_resolve_termination_lists` | A termination dict missing `object_type` or `object_natural_key` is silently dropped. Cable's structural-incomplete check (#32) later flags the cable as skipped, but the operator has no breadcrumb explaining WHY a termination disappeared. | Record a DropEvent when an item is malformed-skipped. |
| `_KNOWN_CF_CACHE` module-global persistence across tests | Medium | upsert.py | The cache is keyed by `base_url` and persists across calls. Test isolation requires explicit `_KNOWN_CF_CACHE.clear()` in `setup_function`. A test that forgets to clear can mask regressions. | Move the cache onto the `NetboxHTTP` instance, or provide a `clear_cf_cache()` helper called from a pytest fixture. (Document as follow-up; the explicit clear is in place for current tests.) |
| `ProgressReporter.bind_auditor` ordering window | Medium | progress.py, driver.py | Driver constructs `Auditor`, then `bind_auditor`, then runs Phase-1. The window is tiny and currently safe (no records logged between construction and bind), but the design depends on call-order discipline. | Pass the auditor to `ProgressReporter` at construction time, removing the bind hop. Requires CLI restructure. |
| `_resolve_polymorphic_id_pairs` does not short-circuit on already-resolved tuple | Low | driver.py:`_resolve_polymorphic_id_pairs` | Only `isinstance(raw_id, int)` short-circuits. A re-entrant pass would re-issue index lookups, wasted work. | Add `tuple`/`list` short-circuit check after int. Low impact (re-entry is unusual). |
| `Phase2Summary.is_clean()` uses raw string keys | Low | phase2.py | Phase-1 uses `UpsertOutcome` enum, Phase-2 uses raw strings (`"failed"`, `"patched"`, `"skipped"`). Asymmetric, invites typos. | Define a `Phase2Outcome` enum and migrate. Cosmetic but improves consistency. |
| `_WARNED_MISSING_FK` module global persists across calls | Low | driver.py | A second `run_import` in the same process silently misses warnings for already-seen triples. CLI is unaffected (fresh process per invocation), but library callers and tests need explicit `.clear()`. | Move the set onto `ImportSummary` so each run has its own deduper. |
| `_iter_jsonl` shim in driver.py is a one-line re-export | Low | driver.py:`_iter_jsonl` | Original justification ("keeping existing call sites happy") is no longer relevant; the only intra-module caller can use `iter_jsonl` directly. | Inline the call, delete the shim. |
| `_known_custom_fields_for` returns `set()` for "no CFs for this CT", which filters out everything | Low | upsert.py | Comment claims this is correct (operator-side ordering ensures CFs exist before write-time), but does not explain the invariant. | Add a one-line invariant note pointing at where the customfield phase runs in plan order. |

## Suggested architectural cleanups

These are larger refactors. Each lands in `TODO.md` as a tracked entry for future scheduling.

1. **ResolveContext dataclass.** Roll up `(http, index, registry, snapshot_index, processing_stack, deferred_queue, auditor, failed_keys, deferred_fields_by_ct, openapi)` into a frozen context object. `_try_lookahead` reduces from 14 kwargs to `(ctx, value, target_ct, child_ct, child_nk, field_name)`. The three call sites for the resolver pre-passes simplify equivalently.
2. **Unified drop-recording helper.** The three call sites that compute `queue_size_before`, call the resolver, then call `_record_drop` should be one helper returning `(rid|None, category|None)`.
3. **BodyPreparer abstraction.** Consolidate write-time body coercion: enum-dict collapse, None drop, custom-fields filter, deferred-field strip. Today these are scattered across `driver.py` and `upsert.py`.
4. **Phase2Outcome enum.** Replace raw string keys in `Phase2Summary.counts` with a proper enum to mirror `UpsertOutcome`.
5. **Pre-resolution deferred-field strip.** Currently `_strip_deferred_fields_and_queue` runs at the END of `_resolve_body`, after FK resolution that might have already dropped the field. Re-architect so deferred fields are stripped from the body BEFORE per-field resolution begins.

## Comment-quality flags

Items below should be revised. "Rescue-10" is operator/internal slang and should be replaced with neutral phrasing. Comments that describe what changed (rather than why) should be rewritten to explain the constraint.

| File:Line | Comment (excerpt) | Issue |
|---|---|---|
| driver.py: `failed_keys` setup | "... which on the rescue-10 import converted a multi-hour retry storm ..." | "rescue-10" is internal slang. Replace with a neutral description ("on a 5000-row import"). |
| lookahead.py: step 3a | "The rescue-10 run showed the same failed Device POST being retried dozens of times ..." | Same slang issue. Replace. |
| driver.py: row count up-front | "even the largest fixture in the rescue-10 set is ~10 MB." | Same slang. |
| driver.py:`_WARNED_MISSING_FK` | `# Module-level "already warned" sentinel, dedupes per (ct, field, target).` | Describes WHAT, not WHY. Should explain operator stderr-noise motivation. |
| upsert.py POST path | Stacked "# Defensive: ..." + "# POST/create path: ..." + "# Task #28: ..." | Three separate comments stacked; the second overlaps `_coerce_body_for_write`'s docstring. Consolidate. |
| progress.py constructor | "This lets the driver pass progress=None to existing tests without polluting their captured stderr." | "Existing tests" justification dates the comment. Rephrase to neutral API contract. |

## Files reviewed

- `src/nbsnap/import_/driver.py`
- `src/nbsnap/import_/upsert.py`
- `src/nbsnap/import_/lookahead.py`
- `src/nbsnap/import_/audit.py`
- `src/nbsnap/import_/progress.py`
- `src/nbsnap/import_/preflight.py`
- `src/nbsnap/import_/phase2.py`
- `src/nbsnap/import_/snapshot_index.py`
- `src/nbsnap/import_/nk_index.py`
- `src/nbsnap/import_/fk_resolve.py`
- `src/nbsnap/import_cli.py`
- `src/nbsnap/reset_cli.py`
- `src/nbsnap/graph/polymorphic.py`

## Remediation plan

**Land in this session** (correctness, high value, contained scope):

1. KeyError vs ValueError disambiguation in `_resolve_body` simple-FK catch
2. `DropEvent(DEFERRED_TO_PHASE2)` emitted alongside `DeferredFK` push in `_strip_deferred_fields_and_queue`
3. M2M drops audited through `_safe_resolve_m2m`
4. New `DropCategory.UPSERT_FAILED` for look-ahead create failures (replaces today's mis-bucketed `MISSING_FROM_SOURCE`)
5. Anchored substring matching in `_classify_post_failure`
6. Audit event when a malformed termination item is skipped
7. Replace "rescue-10" slang in comments
8. Re-comment `_WARNED_MISSING_FK` to explain WHY

**Document in TODO.md** (architectural, larger surface area):

1. ResolveContext dataclass refactor
2. Unified drop-recording helper
3. BodyPreparer extraction
4. Phase2Outcome enum
5. Pre-resolution deferred-field strip ordering
6. `_KNOWN_CF_CACHE` instance-scoping
7. ProgressReporter constructor accepts auditor directly
8. `_WARNED_MISSING_FK` moved onto ImportSummary
9. `_iter_jsonl` shim removal
