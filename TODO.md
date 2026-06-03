# TODO

Outstanding work to deliver the NetBox portable-snapshot tool
(`nbsnap`). The phasing comes from `PLAN.md`. Every open entry is sized
for a 1, 2 hour focused work window. Each entry includes the file or
area it touches, the technical context the implementer needs, the
requirements as a concrete change list, and a testing step. Closed
items move to the Completed section at the end.

ID conventions:

* `INFRA-nn` for repo, CI, dev environment, test stack work.
* `RES-nn` for research and decision tickets that gate downstream
  implementation.
* `FEAT-nn` for feature implementation.
* `TEST-nn` for testing work that is not a side effect of a `FEAT-`.
* `DOC-nn` for documentation deliverables.
* `BUG-nn` for bug fixes (none open yet, reserved).
* `REL-nn` for release and milestone gates.

Sub-tickets carry a lowercase letter suffix on the parent ID, for
example `INFRA-01a`, so a cross-reference from `PLAN.md` to the parent
concept still resolves.

Cross-references:

* `PLAN.md` for phase definitions and exit criteria.
* `docs/` for design documents.
* `docs/frictions/` for friction-area deep-dives.
* `goals.md` for scope and success criteria.

---

## Codebase status

Phases 0 through 3 plus the bulk of Phases 4 through 9 are
implemented and committed. The open backlog below covers the
work that is genuinely ahead, namely the import-resolution
improvements (FEAT-36 series), the destination-reset utility
(FEAT-37 series), the deferred-FK Phase-2 writer (FEAT-23),
a handful of integration tests, and the operator-runbook
documentation set. Run `git log --oneline --grep="^feat\|^fix"`
for the full implementation history.

## Open, Phase 4, Export engine

### [TEST-04] Export reproducibility integration test

**Context:** `goals.md` success criterion 3 / `PLAN.md` Phase 4 exit.

**Requirements:**

- `tests/integration/test_export_reproducibility.py`.
- Seed source stack, run export to `/tmp/a/`, run again to `/tmp/b/`.
- Compare directory trees: every per-type `.jsonl` file must be
  byte-identical. The only allowed delta is `manifest.exported_at`
  (and any timer values).
- Use `difflib.unified_diff` to surface deltas in the assertion
  message.

**Testing:** the test itself is the testing step. Run it twice,
confirm green both times. Mutate a single Device's name on the
source stack between runs, run the test, confirm it fails with a
clear diff.

**Estimated Effort:** 1-2h

### [TEST-05] Renderer-minimum endpoint contract test

**Context:** `goals.md` success criterion 5, every endpoint marked
`M` in `docs/02-data-model-scope.md` must be hit.

**Requirements:**

- `tests/integration/test_renderer_minimum_coverage.py`.
- Monkey-patch `NetboxHTTP.get_all` to record every (method, url) tuple.
- Run `nbsnap export` against the seeded source.
- Build the expected endpoint set from the M-rows of the data-model-scope
  doc (hard-code the list, with a comment pointing back at the doc).
- Assert `recorded >= expected`.

**Testing:** run on a clean run, confirm green. Comment out the
prefix walking in the export engine, confirm the test fails and the
assertion lists the missing endpoints.

**Estimated Effort:** 1-2h

---

## Open, Phase 5, Import engine


### [REFACTOR-01a] Define `ResolveContext` dataclass and build it in `run_import`

**Context:** the code review at
`__doc/code_reviews/20260615-1437_import_code_post_lp_review.md`
flagged the resolver call graph in `src/nbsnap/import_/driver.py`
as a DRY violation. Eight to ten parameters (`http`, `index`,
`registry`, `snapshot_index`, `processing_stack`,
`deferred_queue`, `auditor`, `failed_keys`,
`deferred_fields_by_ct`, `openapi`) thread through five call
sites; one missed forward silently disables a feature. This
sub-ticket lands only the dataclass and its construction
point so subsequent sub-tickets can migrate call sites.

**Requirements:**

- Add a new module
  `src/nbsnap/import_/resolve_context.py` with a frozen
  dataclass `ResolveContext` carrying the ten fields. Use
  `TYPE_CHECKING` imports to avoid runtime cycles with
  `lookahead.py` and `driver.py`.
- In `src/nbsnap/import_/driver.py:run_import`, construct
  `ctx = ResolveContext(http=http, index=index, ...)` once,
  after the look-ahead state objects exist. Do not yet pass
  `ctx` to any callee; migration lands in 01b and 01c.
- Export `ResolveContext` from `nbsnap.import_` via
  `__init__.py`.

**Testing:**

- Create `tests/unit/test_resolve_context.py` with a smoke
  test that builds a `ResolveContext` from mocks and asserts
  each field is accessible.
- Run the existing import test suite, confirm no regression.
- Confirm `from nbsnap.import_ import ResolveContext` works
  from a Python REPL.

**Estimated Effort:** 1-2h.


### [REFACTOR-01b] Migrate `_try_lookahead` and `resolve_or_create` to accept `ResolveContext`

**Context:** sub-ticket of REFACTOR-01. With the dataclass
landed by 01a, migrate the two main consumer call sites:
`src/nbsnap/import_/driver.py:_try_lookahead` and
`src/nbsnap/import_/lookahead.py:resolve_or_create`.

**Requirements:**

- Change `_try_lookahead` to take `ctx: ResolveContext` plus
  the four task-specific args (`value`, `target_ct`,
  `child_ct`, `child_nk`, `field_name`). Read all shared
  state from `ctx`.
- Change `resolve_or_create` to take `ctx: ResolveContext`
  plus `(content_type, natural_key, depth)`. Update the
  inner `_resolve_body` recursive call to forward `ctx`.
- Update the call site in `_resolve_body`'s simple-FK branch
  to build / forward `ctx`.
- Leave the pre-pass call sites (`_resolve_polymorphic_id_pairs`,
  `_resolve_termination_lists`) on the OLD signature by
  having `_try_lookahead` accept BOTH shapes during transition.
  The adapter goes away in 01c.

**Testing:**

- Update tests that drive these functions directly:
  `tests/unit/test_import_lookahead_resolver.py`,
  `tests/unit/test_import_driver_lookahead.py`,
  `tests/unit/test_import_lookahead_failed_cache.py`,
  `tests/unit/test_import_lookahead_body_resolution.py`.
- Run the entire unit-test suite, confirm green.
- Add one regression test asserting a `ResolveContext` built
  with all 10 fields drives a round-trip through
  `_try_lookahead` -> `resolve_or_create` end-to-end.

**Estimated Effort:** 1-2h.


### [REFACTOR-01c] Migrate the two pre-passes onto `ResolveContext` and remove the transition adapter

**Context:** sub-ticket of REFACTOR-01. 01b left a
transition adapter so the pre-passes could keep the old
kwarg shape. This sub-ticket finishes the migration so the
adapter can be deleted.

**Requirements:**

- Change `_resolve_polymorphic_id_pairs` to take
  `ctx: ResolveContext, owner_ct: str`. Read shared state
  from `ctx`; remove the old explicit kwargs.
- Same migration for `_resolve_termination_lists`.
- Update the two pre-pass call sites in `_resolve_body` to
  pass `ctx`.
- Remove the transition adapter from `_try_lookahead`.
- Grep `src/` for the old parameter names to confirm no
  caller remains on the legacy shape.

**Testing:**

- Update tests that drive the pre-passes directly:
  `tests/unit/test_import_polymorphic_id_pairs.py`,
  `tests/unit/test_import_cable_terminations.py`,
  `tests/unit/test_import_deferred_fields_strip.py`.
- Run the full unit suite, confirm green.
- End-to-end: re-run the rescue-10 import and confirm exit 0
  and identical record counts to the postfix8 baseline.

**Estimated Effort:** 1-2h.


### [REFACTOR-02] Unified drop-recording helper

**Context:** `src/nbsnap/import_/driver.py` has three sites
that follow the pattern `queue_size_before = len(deferred_queue) if deferred_queue is not None else 0`,
then call the resolver, then call `_record_drop(...)`. The
boilerplate adds noise to the resolver pre-passes and risks
divergence when one site updates and the others do not.
Depends on REFACTOR-01 landing.

**Requirements:**

- Add a method `resolve_with_audit(value, target_ct, child_ct,
  child_nk, field_name) -> tuple[int | None, DropCategory | None]`
  to `ResolveContext` (or a free function taking ctx). The
  method captures `queue_size_before`, calls `_try_lookahead`,
  then calls `_record_drop` if the look-ahead returned None.
- Update the three call sites in `_resolve_body`'s simple-FK
  branch, `_resolve_polymorphic_id_pairs`, and
  `_resolve_termination_lists` to use the new helper.
- Remove the inlined `queue_size_before` boilerplate at each
  site.

**Testing:**

- Add `tests/unit/test_resolve_with_audit.py` exercising the
  helper directly with mocks for the three outcomes: hit
  (rid set, no category), miss-then-drop (rid None, category
  set), miss-then-deferred (rid None, category
  DEFERRED_TO_PHASE2).
- Run the full unit suite, confirm green; the three migrated
  sites now share the helper.

**Estimated Effort:** 1-2h. Depends on REFACTOR-01.


### [REFACTOR-03a] Introduce `BodyPreparer` skeleton with enum-dict and None-drop chains

**Context:** write-time body coercion is scattered across
`src/nbsnap/import_/driver.py` and `src/nbsnap/import_/upsert.py`:
enum-dict collapse, None drop, custom-fields filter, and
deferred-field strip all live in separate places. This first
sub-ticket lands the class and migrates the two simplest
transforms.

**Requirements:**

- Add `src/nbsnap/import_/body_preparer.py` with a
  `BodyPreparer` class. Constructor takes the parameters
  enum-dict collapse and None-drop need today (none beyond
  the body itself). One method
  `prepare(content_type: str, body: dict) -> dict`.
- Move `_collapse_enum_dict` invocation from
  `src/nbsnap/import_/upsert.py:_coerce_body_for_write` into
  `BodyPreparer.prepare` as the first chain step.
- Move the None-drop transform from `_coerce_body_for_write`
  (currently controlled by `drop_nones`) into the chain as
  the second step. Document the order in the class docstring.
- Update `upsert` to call `BodyPreparer.prepare` instead of
  `_coerce_body_for_write` for the enum-dict + None-drop
  portion. The custom-fields filter and deferred-field strip
  stay where they are; REFACTOR-03b migrates them.

**Testing:**

- Add `tests/unit/test_body_preparer.py` covering the chain
  in isolation: an input with an enum-dict status and a
  None profile becomes a body with a flat status and no
  profile key.
- Keep the existing
  `tests/unit/test_import_drop_nones_on_post.py` tests
  passing.
- Run the rescue-10 import in a quick sanity sweep against a
  cleared destination; confirm exit 0.

**Estimated Effort:** 1-2h.


### [REFACTOR-03b] Move custom-fields filter and deferred-field strip into `BodyPreparer`

**Context:** sub-ticket of REFACTOR-03. With the
`BodyPreparer` skeleton in place from 03a, this ticket moves
the two remaining write-time transforms into the same chain
so `upsert` has no body-coercion responsibility.

**Requirements:**

- Migrate `_filter_custom_fields` from
  `src/nbsnap/import_/upsert.py` into `BodyPreparer.prepare`
  as the third chain step. Pass the destination CF registry
  via the preparer's constructor so test code can inject a
  stub.
- Migrate the deferred-field strip from
  `src/nbsnap/import_/driver.py:_strip_deferred_fields_and_queue`
  into `BodyPreparer.prepare` as the fourth chain step. The
  queue-push side-effect stays on the existing helper; the
  preparer only strips fields from the body.
- Document the exact ordering in the class docstring:
  enum-dict -> None drop -> CF filter -> deferred-field
  strip. Order matters because CF filter looks at the body
  AFTER None drop removes profile-style fields, and the
  deferred-field strip needs the body before CF filter
  could remove a deferred CF.
- Remove `_coerce_body_for_write` and `_filter_custom_fields`
  from `upsert.py` once no caller remains.

**Testing:**

- Extend `tests/unit/test_body_preparer.py` with chain
  ordering tests that pin the four-step order. Cover the
  case where a deferred field carries a `custom_fields`
  sub-dict to confirm the right ordering.
- Update `tests/unit/test_import_custom_fields_filter.py`
  and `tests/unit/test_import_deferred_fields_strip.py` to
  import from the new location.
- Run the rescue-10 import end-to-end, confirm exit 0 and
  Phase-2 patched count matches postfix8.

**Estimated Effort:** 1-2h. Depends on REFACTOR-03a.


### [FEAT-42a] Investigate why dcim.cable plans before dcim.interface and document findings

**Context:** sub-ticket of FEAT-42. Source review R-6.
Tracing postfix runs shows `dcim.cable` at planner position
3 with `dcim.interface` at 17. Every cable termination then
triggers a look-ahead that ultimately fails for ~110
interfaces (rescue-10 baseline). The investigation step is
non-trivial enough to warrant its own ticket so the fix in
FEAT-42b can land in a focused window.

**Requirements:**

- Write a one-shot debug script under
  `__doc/investigations/feat42_plan_order.py` that loads the
  rescue-10 snapshot's OpenAPI schema, builds the graph, and
  prints the plan order side by side with the polymorphic-hint
  synthetic edges. Run it; capture the output.
- Determine whether
  `src/nbsnap/graph/polymorphic.py:POLYMORPHIC_HINTS` already
  emits a cable -> interface edge (it should, since
  `a_terminations` lists `dcim.interface` as a target).
- Confirm whether `add_hint_edges` adds the edge in the
  expected direction (child=cable, parent=interface). If
  yes, the SCC planner should naturally put interface first;
  if not, the bug is in the edge construction.
- Inspect `src/nbsnap/graph/algo.py:plan` and confirm the
  topological sort respects the synthetic edges. Document
  the finding in
  `__doc/investigations/feat42_plan_order.md`.
- The deliverable for this sub-ticket is the report
  document, not a code change.

**Testing:**

- The investigation script's output IS the test. Save the
  printout in the report so future readers can verify.
- If the investigation reveals the planner is correct and
  another factor (manifest ordering, content-type filter)
  drives the cable-first behaviour, name that factor
  explicitly in the report.

**Estimated Effort:** 1-2h.


### [FEAT-42b] Apply the planner reorder fix identified by FEAT-42a and add regression test

**Context:** sub-ticket of FEAT-42. With the investigation
report from FEAT-42a in hand, this sub-ticket applies the
fix and locks the order with a test.

**Requirements:**

- Apply the fix identified in FEAT-42a's report. Three
  likely options, pick the one the investigation supports:
  1. Tighten existing POLYMORPHIC_HINTS entries to add a
     stronger directional edge.
  2. Add a `KNOWN_DEPENDENCY_HINTS` table to
     `src/nbsnap/graph/polymorphic.py` listing
     `(child_ct, parent_ct)` pairs the planner should honour
     even when no schema edge exists. Initial entry:
     `("dcim.cable", "dcim.interface")`.
  3. Adjust the SCC sort's secondary criterion.
- Update CLAUDE.md or design docs if the fix introduces a
  new public concept (e.g. dependency hints).

**Testing:**

- Add
  `tests/unit/test_graph_polymorphic_hints.py::test_cable_orders_after_interface`
  asserting `plan.order.index("dcim.interface") <
  plan.order.index("dcim.cable")` against the rescue-10
  snapshot's schema.
- End-to-end: re-run rescue-10 with DEBUG logging enabled
  and confirm the cable phase shows zero look-ahead
  failures.

**Estimated Effort:** 1-2h. Depends on FEAT-42a.


### [FEAT-43] Audit JSONL flush hardening: 5s interval + signal handler

**Context:** Source review R-7.
`src/nbsnap/import_/progress.py:ProgressReporter` flushes
`audit.jsonl` every 30 seconds (`_AUDIT_FLUSH_INTERVAL_SECONDS = 30.0`).
A hard kill between flushes loses up to 30 seconds of audit
events. On a 40-minute import, that is ~1.25% of records
potentially lost from the diagnostic trail.

**Why this matters:** unattended imports in containers can be
terminated by external supervisors (OOMKilled, deploy
restart). The audit trail is the operator's primary forensic
tool when a run is killed; a 30-second gap exactly at the
moment of crash defeats the purpose.

**Requirements:**

- Lower `_AUDIT_FLUSH_INTERVAL_SECONDS` from `30.0` to `5.0`
  in `progress.py`. Disk-write cost is negligible (append-only,
  small per row).
- Wire a SIGTERM and SIGINT handler in
  `src/nbsnap/import_cli.py:run_import_cli` that calls
  `progress.close()` before re-raising. Use Python's
  `signal.signal()` carefully, the handler should be idempotent
  in case the driver also flushed already.
- Add an `--audit-fsync` opt-in CLI flag that calls `os.fsync()`
  after every flush for paranoid deployments where the host
  filesystem might not survive the kill.
- Document the new cadence in `progress.py`'s module
  docstring.

**Testing:** unit test
`tests/unit/test_import_progress.py::test_audit_flushes_within_interval`
asserts the file is non-empty after one tick + 6 seconds.
Integration test: start an import in a subprocess, SIGTERM
it 10 seconds in, confirm the audit JSONL exists and contains
the recorded events.

**Estimated Effort:** 1-2h.


### [FEAT-44] Progress ticks carry timestamps and per-phase throughput

**Context:** Source review R-8.
`src/nbsnap/import_/progress.py:ProgressReporter.tick` and
`start_phase`/`end_phase` emit content without any timestamp
or throughput indicator. An operator watching a long-running
import cannot detect rate degradation that would otherwise
signal a NetBox-side issue.

**Why this matters:** A NetBox instance gradually exhausting
memory will slow imports asymptotically. Without per-phase
throughput numbers, the operator sees the same progress
output as a healthy run and misses the warning sign.

**Requirements:**

- Prepend each tick line with an ISO-8601 timestamp:
  `[14:35:17] #   dcim.interface 100/3582`. Use
  `datetime.now().isoformat(timespec="seconds")` or just
  `datetime.now().strftime("%H:%M:%S")` for compactness.
- Track `phase_start_at` in `ProgressReporter` and emit a
  per-phase trailer on `end_phase`:
  `# Phase dcim.interface complete: 3582 records in 23m17s (2.55/s)`.
- Add rate-degradation detection: keep a rolling-60s record
  counter; on each tick after the first minute, compare the
  current rate to the phase's overall rate. If below 50%,
  emit one WARNING `# Phase dcim.interface rate degraded:
  X/s (was Y/s)`. Suppress further warnings for the same
  phase to avoid noise.
- Add an `--no-timestamps` opt-out for log aggregators that
  apply their own timestamps.

**Testing:** unit tests with a swappable clock pinning the
tick format and the rate-degradation threshold. Manual: run
rescue-10 and confirm the per-phase trailer shows expected
durations.

**Estimated Effort:** 2h.


### [FEAT-45a] Tag `UpsertResult` with HTTP status and skip caching transient (5xx) failures

**Context:** sub-ticket of FEAT-45. Source review R-10.
`src/nbsnap/import_/lookahead.py:resolve_or_create` caches
every FAILED outcome in `failed_keys` permanently. A
transient 5xx caches the key as if it were a permanent 4xx
rejection. This sub-ticket adds the structural plumbing and
the simple fix (do not cache 5xx).

**Requirements:**

- Extend `UpsertResult` in `src/nbsnap/import_/upsert.py`
  with an `http_status: int | None` field. Default `None`
  for outcomes where no HTTP call fired (SKIPPED).
- Update the POST and PATCH exception handlers in `upsert`
  to extract the status code from `NetboxHTTPError.status`
  and attach it to the result.
- In `resolve_or_create`, only add to `failed_keys` when
  `result.http_status is None or 400 <= result.http_status < 500`.
  Skip 5xx so the next look-ahead retries.
- Document the policy in the `resolve_or_create` docstring.

**Testing:**

- Extend
  `tests/unit/test_import_lookahead_failed_cache.py` with a
  test that simulates a 503 response and asserts the key is
  NOT in `failed_keys` after the call.
- Add a paired test asserting a 400 response IS cached.

**Estimated Effort:** 1-2h.


### [FEAT-45b] Add `UPSERT_FAILED_TRANSIENT` audit category and `--no-lookahead-failure-cache` flag

**Context:** sub-ticket of FEAT-45. With the 5xx-skip
behaviour from 45a, this sub-ticket surfaces transient
failures in the audit and adds an operator escape hatch.

**Requirements:**

- Add `UPSERT_FAILED_TRANSIENT` to `DropCategory` in
  `src/nbsnap/import_/audit.py`.
- Update `_record_drop` in `driver.py` to emit
  `UPSERT_FAILED_TRANSIENT` when the failed key was a 5xx
  (read the status from the UpsertResult tracked on the
  failed-keys side, or from a parallel map). Keep
  `UPSERT_FAILED` for the 4xx case.
- Add CLI flag `--no-lookahead-failure-cache` in
  `src/nbsnap/import_cli.py`. When set, `failed_keys` is
  never populated; every look-ahead retries.
- Surface both new categories in the CLI summary.

**Testing:**

- Extend `tests/unit/test_import_audit_split.py` with a
  test asserting a 5xx-flavoured drop is classified as
  `UPSERT_FAILED_TRANSIENT`.
- Add a unit test asserting the CLI flag disables the
  cache entirely.

**Estimated Effort:** 1-2h. Depends on FEAT-45a.


### [FEAT-46a] Implement schema-diff helper that compares two OpenAPI schemas at field-FK level

**Context:** sub-ticket of FEAT-46. Source review R-11.
The preflight check today only compares `netbox-version`
strings. This first sub-ticket lands the pure diff helper
so subsequent sub-tickets can wire it into preflight and
the CLI without re-engineering during integration.

**Requirements:**

- Add `src/nbsnap/schema/diff.py` with a function
  `diff_schemas(snapshot: OpenAPI, destination: OpenAPI,
  scope: set[str]) -> list[FieldDrift]`.
- `FieldDrift` is a dataclass with
  `{content_type, field, snapshot_shape, destination_shape}`.
- Iterate every (content_type, field) in scope. For each,
  call `OpenAPI.field_spec` on both schemas and compare the
  `fk_target` plus `is_m2m` shape. Record any difference.
- Document the diff's semantics in the module docstring,
  including the ground-truth read of "what fields trip
  field_spec into returning different fk_target".

**Testing:**

- Add `tests/unit/test_schema_diff.py` with two synthetic
  OpenAPI documents where one field's `$ref` points at a
  different target. Assert `diff_schemas` returns one
  FieldDrift entry.
- Add a test asserting an identical pair of schemas
  produces zero drift.

**Estimated Effort:** 1-2h.


### [FEAT-46b] Wire `diff_schemas` into preflight and surface `schema_drift` on the report

**Context:** sub-ticket of FEAT-46. 46a landed the helper.
This sub-ticket runs it during preflight and emits the
findings without yet making them block the import.

**Requirements:**

- In `src/nbsnap/import_/preflight.py:run_preflight`, fetch
  the destination's OpenAPI via
  `GET /api/schema/?format=json` and call `diff_schemas`
  against the snapshot's OpenAPI scoped to
  `manifest.counts.keys()`.
- Add `PreflightReport.schema_drift: list[FieldDrift]`
  (default empty). Populate from the diff result.
- Surface findings in the CLI summary block, after the
  existing preflight version skew line. Display each entry
  as one line `schema drift: <ct>.<field> snapshot=<x>
  destination=<y>`.
- Cap at 10 lines with `... and N more` trailer (matches
  FEAT-48's pattern).
- Do NOT block the import yet; `is_blocking` ignores
  schema_drift in this sub-ticket.

**Testing:**

- Extend `tests/unit/test_preflight_enum_dict.py` (or add a
  new file) with a test that asserts `schema_drift` is
  populated when the destination schema differs.
- Integration: re-run rescue-10; confirm no drift entries
  surface (same NetBox 4.6.2 on both sides).

**Estimated Effort:** 1-2h. Depends on FEAT-46a.


### [FEAT-46c] Add `--strict-schema` and `--use-destination-schema` CLI flags

**Context:** sub-ticket of FEAT-46. 46b populated the drift
list as informational; this sub-ticket gives the operator
control over how the tool reacts.

**Requirements:**

- Add `--strict-schema` flag to
  `src/nbsnap/import_cli.py:add_import_args`. When set,
  any non-empty `schema_drift` causes
  `PreflightReport.is_blocking` to return True and the CLI
  exits EXIT_PREFLIGHT_BLOCKED.
- Add `--use-destination-schema` flag. When set, the
  driver loads the destination's OpenAPI (the one already
  fetched in 46b) and uses that as the openapi handle
  passed into `_resolve_body`. The snapshot's schema is
  still loaded for preflight comparison.
- Document both flags in the CLI help text. Add a brief
  note pointing at the operator runbook.

**Testing:**

- Add `tests/unit/test_import_cli_exit_codes.py` cases
  asserting `--strict-schema` exits non-zero when drift is
  present.
- Add a unit test asserting `--use-destination-schema`
  routes the destination's OpenAPI through the driver.

**Estimated Effort:** 1-2h. Depends on FEAT-46b.


### [BUG-07] Phase-2 PATCH treats 2xx as success without verifying field update

**Context:** Source review R-14.
`src/nbsnap/import_/phase2.py:run_phase2` issues
`http.patch(f"{endpoint}{child_id}/", {entry.field_name: target_id})`
and counts any 2xx as `patched`. NetBox can return 200 OK on
a PATCH that did not change a field (e.g. silently ignored
because the target_id exists but is not a legal value for
that field type, depending on NetBox's serializer
behaviour). The audit then reports false confidence.

**Why this matters:** the audit's "phase2: patched=242"
becomes unreliable as a signal. An operator who trusts the
counter as authoritative proof of cycle-closure will be
misled.

**Requirements:**

- After PATCH, GET the record back and confirm the response
  body's `<field_name>` matches the submitted `target_id`.
  NetBox returns the updated record body in the PATCH response
  itself, inspect that first; fall back to a GET if the body
  is empty or stripped.
- Add a new `Phase2Outcome.VERIFIED_MISMATCH` (depends on
  REFACTOR-04) for cases where 2xx came back but the field did
  not actually change.
- Treat VERIFIED_MISMATCH as a non-zero exit code trigger;
  log at WARNING and surface in the summary.
- Add an opt-out `--no-phase2-verify` flag for operators
  willing to trust the 2xx response (matches REST optimism).

**Testing:** unit test in `tests/unit/test_import_phase2.py`
with a fake http that returns 200 OK but the response body
shows the field unchanged. Assert the outcome is
VERIFIED_MISMATCH.

**Estimated Effort:** 2h. Depends on REFACTOR-04 for the
enum.


### [FEAT-49] Exit-code bitmask reflecting SKIPPED granularity and bypass

**Context:** Source review R-17.
`src/nbsnap/import_cli.py:_compute_exit_code` returns 0 or
2 today. SKIPPED outcomes are treated as success even though
they represent data loss. Operators cannot apply per-condition
policy without parsing the audit log.

**Why this matters:** CI/CD pipelines need granular exit
codes to differentiate "clean import" from "import with
known-policy skips" from "import with row failures".

**Requirements:**

- Choose between two approaches:
  (a) Add discrete exit codes:
      - 0 OK
      - 1 EXIT_PREFLIGHT_BLOCKED (existing)
      - 2 EXIT_ROW_FAILURES (existing)
      - 3 EXIT_SKIPPED_OVER_THRESHOLD (proposed, see FEAT-41)
      - 4 EXIT_BLOCKED_BY_SOURCE_GUARD (existing in reset_cli)
      - 5 EXIT_UNEXPECTED (existing)
      - 6 EXIT_SCHEMA_DRIFT_BLOCKED (proposed, see FEAT-46)
      - 7 EXIT_BYPASS_USED (proposed)
  (b) Bitmask: bit 0 failure, bit 1 skip, bit 2 preflight
      bypass, bit 3 schema drift. Combinations expressible.
- Pick (a) for simplicity; document in CLI help and runbook.
- Update `_compute_exit_code` to compute the new codes from
  `ImportSummary` fields.
- Update DOC-01 runbooks once exit codes stabilise.

**Testing:** extend
`tests/unit/test_import_cli_exit_codes.py` with cases for
each new code. Document the matrix in the test docstring.

**Estimated Effort:** 1h after FEAT-41, FEAT-46, FEAT-47
land.


---


### [TEST-06] Idempotency two-run integration test

**Context:** `goals.md` success criterion 2.

**Requirements:**

- `tests/integration/test_import_idempotency.py`.
- Seed source, export, import to fresh destination, inspect audit
  log assert every result is CREATED.
- Re-run import without changes, inspect audit log assert every
  result is NOOP and no PATCH was sent.

**Testing:** run the test, confirm green. Inject a deliberate
extra field on one record in the audit comparison code, confirm the
test fails with a clear pointer to the offending content type.

**Estimated Effort:** 1-2h

### [TEST-07] Cycle resolution end-to-end integration test

**Context:** `docs/frictions/01`. The headline test for the cycle
machinery.

**Requirements:**

- `tests/integration/test_import_cycles.py`.
- Seed source with a Device + Interface + IPAddress chain where
  Device.primary_ip4 points at the IP.
- Export, import.
- GET the device on the destination, assert `primary_ip4.address`
  matches the source's value.
- Repeat for IPv6 if seeded.

**Testing:** run the test, confirm green. Comment out the Phase 2
writer call in `run_import`, re-run, confirm the test fails with a
clear assertion message.

**Estimated Effort:** 1-2h


### [REFINED] [TEST-10] Integration test for demand-driven import order

#### Architectural specification

End-to-end proof that FEAT-36a (snapshot index), FEAT-36b
(look-ahead resolver), FEAT-36c (Phase-2 wiring), and FEAT-23
(Phase-2 writer) compose into a working single-pass import,
even when the snapshot's content-type order is hostile to the
naive sequential importer.

The test runs against the netbox-docker integration stack
already wired up by INFRA-03 (`make stack-up stack-wait`). We
do not need the production NetBox; the test stack is exactly
the controlled environment for this kind of assertion.

Three things the test pins:

1. **Forward references resolve.** A snapshot whose
   `dcim/devices.jsonl` comes before `dcim/sites.jsonl` on
   disk still imports cleanly. The demand-driven resolver
   creates the site on the destination before posting the
   device that references it.
2. **Cycles get deferred and patched.** A device whose
   `primary_ip4` points at its own interface IP (the Device
   ↔ IPAddress ↔ Interface ↔ Device cycle) lands on the
   destination AND has its `primary_ip4` set after Phase-2.
3. **Audit reports the right categories.** No `MISSING_FROM_SOURCE`
   drops; some `OUT_OF_SCOPE` drops (region) are expected and
   acceptable; `DEFERRED_TO_PHASE2` matches the size of the
   deferred queue.

#### REST API details

The test exercises every NetBox endpoint the import path uses:

| Verb | Endpoint | When |
| :--- | :--- | :--- |
| GET  | `/api/status/` | preflight version check |
| GET  | `/api/core/object-types/` or `/api/extras/object-types/` | preflight content-type coverage |
| GET  | `/api/<ct>/?brief=true&limit=500` | NKIndex build (per ct touched) |
| POST | `/api/<ct>/` | record creation (Phase-1 + look-ahead) |
| GET  | `/api/<ct>/<id>/` | upsert skip-if-equal detail fetch |
| PATCH | `/api/<ct>/<id>/` | upsert minimal-diff write, Phase-2 cycle close |

All against `http://localhost:8081` (the netbox-docker dest).
Documentation:
* REST overview: `https://netboxlabs.com/docs/netbox/integrations/rest-api/`
* Per-endpoint shapes: `https://demo.netbox.dev/api/schema/swagger-ui/`

#### Implementation

```python
# tests/integration/test_import_demand_driven.py
"""End-to-end demand-driven import.

Run against the netbox-docker test stacks (require_stack). The
snapshot we feed is intentionally misordered (devices before
sites, cables before interfaces) so the naive sequential
importer would fail; we are proving the look-ahead resolver
compensates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.audit import DropCategory
from nbsnap.import_.driver import run_import
from nbsnap.import_.upsert import UpsertOutcome
from nbsnap.schema.openapi import SCHEMA_PATH
from nbsnap.schema.status import VersionSkew

DEST_URL = "http://localhost:8081"
DEST_TOKEN = "abcdef0123456789abcdef0123456789abcdef01"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, sort_keys=True) + "\n")


def _seed_misordered_snapshot(snap: Path) -> None:
    """Build a snapshot directory where devices reference a site
    that the snapshot's OWN jsonl order will not have created
    yet by the time the device is processed. The look-ahead
    resolver should create the site on demand."""

    # The schema can be the test stack's own openapi.json since
    # we are exercising the resolver against a real NetBox.
    schema_resp = requests.get(
        f"{DEST_URL}/api/schema/?format=json",
        headers={"Authorization": f"Token {DEST_TOKEN}"},
        timeout=30,
    )
    schema_resp.raise_for_status()
    (snap / "schema").mkdir(parents=True, exist_ok=True)
    (snap / "schema" / "openapi.json").write_text(
        json.dumps(schema_resp.json()), encoding="utf-8"
    )

    # Manifest with both content types in counts so the topo
    # plan considers them. We omit a deferred_edges list to keep
    # the test focused on demand-driven (not planner) ordering.
    (snap / "manifest.json").write_text(json.dumps({
        "version": 1,
        "source_url": "https://test-source/",
        "netbox_version": "4.6.2",
        "nbsnap_version": "0.0.1",
        "created_at": "2026-06-15T00:00:00+00:00",
        "counts": {"dcim.site": 1, "dcim.device": 1, "dcim.devicerole": 1,
                   "dcim.manufacturer": 1, "dcim.devicetype": 1},
        "perf": {},
        "deferred_edges": [],
    }), encoding="utf-8")

    # Site that the device will refer to.
    _write_jsonl(snap / "dcim/sites.jsonl", [
        {"natural_key": ["test-hall"],
         "body": {"name": "Test Hall", "slug": "test-hall", "status": "active"}},
    ])
    _write_jsonl(snap / "dcim/devicerole.jsonl", [
        {"natural_key": ["test-role"],
         "body": {"name": "Test Role", "slug": "test-role", "color": "808080"}},
    ])
    _write_jsonl(snap / "dcim/manufacturers.jsonl", [
        {"natural_key": ["test-mfr"],
         "body": {"name": "Test Mfr", "slug": "test-mfr"}},
    ])
    _write_jsonl(snap / "dcim/device-types.jsonl", [
        {"natural_key": [["test-mfr"], "test-model"],
         "body": {"manufacturer": ["test-mfr"], "model": "Test Model",
                  "slug": "test-model"}},
    ])
    _write_jsonl(snap / "dcim/devices.jsonl", [
        {"natural_key": [["test-hall"], "test-dev-1"],
         "body": {"name": "test-dev-1",
                  "site": ["test-hall"],
                  "role": ["test-role"],
                  "device_type": [["test-mfr"], "test-model"],
                  "status": "active"}},
    ])


@pytest.mark.usefixtures("require_stack")
def test_demand_driven_imports_misordered_snapshot(tmp_path: Path) -> None:
    """Even when files are visited in alphabetical order, the
    look-ahead resolver creates referenced parents on demand so
    nothing is dropped MISSING_FROM_SOURCE."""

    snap = tmp_path / "snap"
    snap.mkdir()
    _seed_misordered_snapshot(snap)

    http = NetboxHTTP(DEST_URL, DEST_TOKEN, verify_tls=False)
    summary = run_import(
        http, snap, max_skew=VersionSkew.MINOR, on_error="continue",
    )

    # Pin (1): everything in scope landed on the destination.
    assert summary.counts.get(UpsertOutcome.CREATED, 0) >= 5
    assert summary.counts.get(UpsertOutcome.FAILED, 0) == 0, [
        f.message for f in summary.failures
    ]

    # Pin (3): no MISSING_FROM_SOURCE in the audit; OUT_OF_SCOPE
    # is acceptable (region, vrf, etc.), DEFERRED_TO_PHASE2 is fine.
    if getattr(summary, "auditor", None) is not None:
        missing = [
            ev for ev in summary.auditor.events
            if ev.category is DropCategory.MISSING_FROM_SOURCE
        ]
        assert missing == [], [
            f"{ev.child_content_type}.{ev.field_name} -> "
            f"{ev.target_content_type} NK={ev.target_nk}"
            for ev in missing
        ]


@pytest.mark.usefixtures("require_stack")
def test_phase2_closes_primary_ip4_cycle(tmp_path: Path) -> None:
    """Device.primary_ip4 cycle: device lands, IP lands, Phase-2
    PATCHes primary_ip4 onto the device."""

    # Re-use the seeded INFRA-03 fixture data (D39A) by running
    # `make stack-seed` ahead of this test; the docker fixture
    # already has a Device d39a with primary_ip4 set to
    # 172.16.1.10/24 on its Vlan600 interface.

    src = NetboxHTTP("http://localhost:8080",
                     "0123456789abcdef" * 2 + "01234567",
                     verify_tls=False)
    dst = NetboxHTTP(DEST_URL, DEST_TOKEN, verify_tls=False)
    snap = tmp_path / "snap"

    from nbsnap.export.driver import run_export
    run_export(src, snap)
    summary = run_import(dst, snap, max_skew=VersionSkew.MINOR, on_error="continue")

    # Phase-2 should have patched the cycle-closing primary_ip4.
    assert summary.phase2 is not None
    assert summary.phase2.counts.get("patched", 0) >= 1
    assert summary.phase2.is_clean()

    # Cross-verify against the destination GET.
    resp = requests.get(
        f"{DEST_URL}/api/dcim/devices/?name=d39a",
        headers={"Authorization": f"Token {DEST_TOKEN}"},
        timeout=10,
    )
    devices = resp.json()["results"]
    assert devices, "d39a not on destination"
    assert devices[0]["primary_ip4"] is not None
    assert devices[0]["primary_ip4"]["address"] == "172.16.1.10/24"
```

The fixture in `tests/integration/conftest.py` already defines
`require_stack`, so these tests skip cleanly when the
docker stack is down.

#### Acceptance criteria

* `make stack-up stack-wait stack-seed && pytest
  tests/integration/test_import_demand_driven.py -v` runs both
  tests green against a fresh netbox-docker pair.
* On a workstation without the stack, both tests skip with the
  standard "netbox-docker stack is not running" message; the
  rest of the suite is unaffected.
* If FEAT-36b is regressed, the first test fails on the
  `MISSING_FROM_SOURCE` assertion with a list of which FK
  fields fell through.

**Estimated Effort:** 1-2h

---

## Open, Phase 6, Verification

### [TEST-08a1] Renderer-parity topology fixture, Sites through Devices

**Context:** the headline acceptance gate's reference dataset starts
with the static topology (Sites, Locations, Racks, hardware types,
Devices). Q24 burndown selected the hand-built synthetic shape.

**Requirements:**

- Create `tests/fixtures/renderer-parity/01-sites.json` with one
  Site `hall-d` (`name = "Hall D"`).
- Create `tests/fixtures/renderer-parity/02-locations.json` with
  two Locations, `the-forge` (slug `the-forge`, name `The Forge`),
  `mirage-palace` (slug `mirage-palace`, name `Mirage Palace`),
  both in `hall-d`.
- Create `tests/fixtures/renderer-parity/03-racks.json` with four
  Racks, `D39` and `D40` in `the-forge`, `D55` and `D56` in
  `mirage-palace`.
- Create `tests/fixtures/renderer-parity/04-manufacturers.json` with
  `cisco` and `juniper`.
- Create `tests/fixtures/renderer-parity/05-device-types.json` with
  `cisco/ws-c2950t-24` (for access switches) and
  `juniper/ex4100-24t` (for the dist switch).
- Create `tests/fixtures/renderer-parity/06-device-roles.json` with
  `access_switch` and `distribution_switches`.
- Create `tests/fixtures/renderer-parity/07-devices.json` with
  eight access Devices (two per Rack, slot `A` and `B`, names
  `D39A` through `D56B`) plus one dist Device
  `D-THE-FORGE-SW`. All Devices reference their Site, Location,
  Rack, role, device_type from the previous fixture files.

**Testing:** run `python tests/fixtures/seed.py --url <source-url>
--token <source-token> --dir tests/fixtures/renderer-parity/`,
confirm the seeder lands all seven files without errors. Confirm
`GET /api/dcim/devices/?role=access_switch` returns 8 matches and
`?role=distribution_switches` returns 1.

**Estimated Effort:** 1-2h

### [TEST-08a2] Renderer-parity addressing fixture, Interfaces and IPAddresses

**Context:** the second window covers the L2/L3 addressing layer.
Each access switch needs a Vlan600 SVI with a per-switch IPAddress,
the dist switch needs its `ge-0/0/N` ports and the corresponding
`irb.600` SVI.

**Requirements:**

- Create `tests/fixtures/renderer-parity/08-vlans.json` with one
  VLAN `vlan-600` (`vid = 600`, `name = "MGMT"`).
- Create `tests/fixtures/renderer-parity/09-prefixes.json` with one
  Prefix `172.16.1.0/24`, role `kea-dist-mgmt`.
- Create `tests/fixtures/renderer-parity/10-interfaces.json` with,
  one `Vlan600` Interface per access Device (8 total),
  one `Gi0/2` Interface per access Device (uplink),
  eight `ge-0/0/N` Interfaces on the dist Device (N = 0..7),
  one `irb.600` Interface on the dist Device.
- Create `tests/fixtures/renderer-parity/11-ip-addresses.json` with
  eight access IPAddresses (`172.16.1.10/24` through
  `172.16.1.17/24`) assigned to the matching Vlan600 SVIs, plus
  one dist IPAddress (`172.16.1.1/24`) assigned to `irb.600`.

**Testing:** run the seeder, confirm
`GET /api/dcim/interfaces/?device=D39A&name=Vlan600` returns 1
match. Confirm
`GET /api/ipam/ip-addresses/?address=172.16.1.10/24` returns 1
match assigned to `D39A`'s Vlan600 interface. Spot-check the dist
SVI `irb.600` has `172.16.1.1/24`.

**Estimated Effort:** 1-2h

### [TEST-08a3] Renderer-parity cabling fixture and nb2kea verify pass

**Context:** the third window connects the access switches to the
dist switch via Cables and confirms the dataset is renderable
through nb2kea's existing verification script. The verify pass is
the gate that proves the fixture is valid for `TEST-08c`.

**Requirements:**

- Create `tests/fixtures/renderer-parity/12-cables.json` with eight
  Cables. Each connects one access Device's `Gi0/2` to the
  matching dist Device port (`D39A:Gi0/2` to
  `D-THE-FORGE-SW:ge-0/0/0`, then `D39B` to `ge-0/0/1`, and so on
  through `D56B` to `ge-0/0/7`). Use the polymorphic termination
  resolver from the seeder.
- Each dist-side Interface carries an `untagged_vlan` set to
  `vlan-600`, plus a `description` of the form
  `TABLE; D<rack>-<slot>` matching the nb2kea Option 82 convention
  (e.g. `TABLE; D39-A`).
- Run `__reference/nb2kea/scripts/netbox_verify_renderable.py`
  against the seeded source stack as a one-shot validation. Treat
  any error output as a fixture defect, not a bug in the renderer.

**Testing:** run the seeder, confirm
`GET /api/dcim/cables/` returns 8 cables. Then run
`NB_URL=<source-url> NB_TOKEN=<source-token> python
__reference/nb2kea/scripts/netbox_verify_renderable.py`, confirm
exit code 0. Capture the stdout in the test report so a future
fixture drift is visible.

**Estimated Effort:** 1-2h

### [TEST-08b] Run nb2kea renderers against the source as a subprocess

**Context:** the test invokes `__reference/nb2kea/`'s scripts as
subprocesses against the source stack.

**Requirements:**

- `tests/integration/test_renderer_parity_source.py`.
- Wrapper that runs `python __reference/nb2kea/scripts/netbox2cisco.py`,
  `netbox2junos.py`, `netbox2kea.py` against the source stack via
  `NB_URL` and `NB_TOKEN` env vars.
- Capture rendered output into a temp dir.
- Assert each script exits 0 and produces the expected file count
  (one per Device for netbox2cisco / netbox2junos, one global file
  for netbox2kea).

**Testing:** run the test against the seeded source stack, confirm
green. Mutate `__reference/nb2kea/scripts/netbox2cisco.py` to exit 1,
confirm the test fails.

**Estimated Effort:** 1-2h

### [TEST-08c1] Renderer parity roundtrip orchestration

**Context:** the first window of the acceptance gate runs the
roundtrip itself, source export then destination import. The
output of this step is two NetBox stacks holding the same
modelled network. The renderer execution and the diff live in
`TEST-08c2` and `TEST-08c3`.

**Requirements:**

- Create `tests/integration/test_renderer_parity_roundtrip.py`.
- Fixture `seeded_source` that brings the source stack up via
  `make stack-up stack-wait`, then runs the
  `TEST-08a1`/`a2`/`a3` seed fixtures against it.
- Fixture `empty_destination` that brings the destination stack up
  with no seed (the destination starts clean for the cold
  migration).
- Test function `test_roundtrip_lands_clean` that calls the
  `nbsnap.verify.roundtrip` helper from `FEAT-27a` with source
  and destination clients. Assert the call returns
  `RoundtripResult(success=True, deltas=[])`.
- Assert object counts match across the stacks for one canonical
  type (`dcim.device` count from source equals destination).
- Capture the import audit log at
  `<work_dir>/_import.audit.jsonl` and stash the path on the
  pytest record for later windows.

**Testing:** the test function above is the testing step. Run
`pytest tests/integration/test_renderer_parity_roundtrip.py::test_roundtrip_lands_clean -q`,
confirm green. Inspect the audit log, confirm every entry's
result is `CREATED`.

**Estimated Effort:** 1-2h

### [TEST-08c2] Run nb2kea renderers against the destination

**Context:** the second window invokes the three nb2kea renderers
against the destination stack (which holds the imported snapshot
state from `TEST-08c1`). The output lands in a temp directory
the diff step can compare against the source-side output from
`TEST-08b`.

**Requirements:**

- Extend `tests/integration/test_renderer_parity_roundtrip.py`
  with `test_renderers_against_destination`.
- For each of `netbox2cisco.py`, `netbox2junos.py`,
  `netbox2kea.py` under `__reference/nb2kea/scripts/`, run via
  `subprocess.run` with `env={"NB_URL": dest_url, "NB_TOKEN":
  dest_token, ...}` and `cwd=tmp_path`.
- Capture stdout, stderr, and the rendered output files into
  `<tmp_path>/dest-rendered/`.
- Assert every renderer exits 0. The file count matches the
  source-side count from `TEST-08b` (one per Device for
  netbox2cisco / netbox2junos, one global for netbox2kea).
- Reuse the `seeded_source` and `empty_destination` fixtures
  from `TEST-08c1`. The roundtrip from `TEST-08c1` must run
  before this test, declare the dependency with
  `pytest.mark.dependency`.

**Testing:** run the test function, confirm exit 0 for all
renderers and the expected file counts. Pollute the destination
(delete one Device's interfaces) between roundtrip and renderer
run, confirm the renderers either fail or produce divergent
output that `TEST-08c3` catches.

**Estimated Effort:** 1-2h

### [TEST-08c3] Diff the rendered output trees with banner whitelist

**Context:** the third and final window asserts byte equality
between source-side and destination-side renderer output, modulo
the known banner lines that name the source or destination
NetBox hostname (the renderers print `NETBOX_HOST` at the top of
each output).

**Requirements:**

- Extend `tests/integration/test_renderer_parity_roundtrip.py`
  with `test_rendered_outputs_match`.
- Compare `<tmp_path>/source-rendered/` (from `TEST-08b`) against
  `<tmp_path>/dest-rendered/` (from `TEST-08c2`).
- Use `difflib.unified_diff` for each matched filename pair.
- Banner whitelist, lines matching the regex
  `^(\\#|//|!) .* netbox(\.|/)` are normalised through
  `re.sub(r"https?://[^/\\s]+", "https://NETBOX_HOST",
  line)` before the compare.
- Assert the diff list is empty after whitelisting.
- On non-empty diff, attach the diff to the pytest failure
  message so CI shows the divergence.

**Testing:** run end-to-end, confirm green on a clean roundtrip.
Drop the Phase 2 deferred-FK writer call in `nbsnap.import_`,
re-run, confirm the test catches the divergence on
`Device.primary_ip4` paths through the renderer output.

**Estimated Effort:** 1-2h

---

## Open, Phase 7, Operational polish

### [DOC-01a] Operator runbook, cold migration workflow

**Context:** `PLAN.md` Phase 7 exit. Split the runbook by workflow.

**Requirements:**

- `docs/operator-runbook.md` opens with a Safety section per Q25
  burndown. The Safety section carries the production-read-only
  banner verbatim from `CLAUDE.md` and links back as the canonical
  source. The Safety section is at the very top of the runbook
  file, before any workflow heading.
- Add the "Cold migration" workflow heading. Open the section with
  the one-line link "see Safety section above" before any command.
- Steps: prepare destination NetBox (empty), set env vars, run
  preflight, run export, run import, run verify.
- Commands fully spelled out, including the four env vars from
  `CLAUDE.md`.
- Rollback procedure: clear destination via `psql` (acceptable,
  destination is freshly installed and the rollback is a re-deploy).

**Testing:** dry-run the runbook against the test stack. Confirm
each command produces the expected output. Have one teammate read
it and run it without asking questions.

**Estimated Effort:** 1-2h

### [DOC-01b] Operator runbook, parallel deployment workflow

**Context:** the source and destination are sibling NetBoxes serving
different sites.

**Requirements:**

- Extend `docs/operator-runbook.md` with a "Parallel deployment"
  workflow heading. Open with the "see Safety section above" link.
- Step through the install-local flag review (network-only scope,
  so the only category is `IPAddress.dns_name` matching the source
  host per `FEAT-13a`): which entries to keep, which to drop,
  which to rewrite via `--replacement-map`.
- Document the `--allow-source-install-ips` posture and when it is
  acceptable.

**Testing:** dry-run with two test stacks where source IPAMs the
source's own hostname. Verify the operator-facing flag file lists
the finding and the runbook's review step catches it.

**Estimated Effort:** 1-2h

### [DOC-01c] Operator runbook, partial re-sync workflow

**Context:** source has changed, destination needs the delta only.

**Requirements:**

- Extend `docs/operator-runbook.md` with a "Partial re-sync"
  workflow heading. Open with the "see Safety section above" link.
- Step through: incremental export (full re-export, the format is
  cheap to diff), import with `--reject-existing` off so existing
  rows PATCH, verify with `diff`.
- Document the audit log location and how to grep for `PATCHED`
  outcomes to confirm the delta landed.

**Testing:** dry-run with a single mutated Device on the source,
follow the runbook end-to-end, confirm only that one Device is
PATCHED on the destination.

**Estimated Effort:** 1-2h

### [DOC-02a] Performance guide, NetBox-side tuning sections

**Context:** the operator tunes NetBox itself before reaching for
front-proxy or tool-side knobs. This window covers the two
NetBox-side levers, page size and database connection pool.

**Requirements:**

- Create `docs/operator-performance.md` if absent.
- Section "MAX_PAGE_SIZE tuning". Document how NetBox's
  configuration setting interacts with the nbsnap
  `--page-size` flag (`FEAT-17a`). Give a measurement command
  `time nbsnap export --page-size 500` versus
  `--page-size 1000`. Recommend a starting value of 500 with the
  trade-off noted (smaller pages reduce N+1 cost, larger pages
  reduce round-trip count).
- Section "PostgreSQL connection pool sizing". Document the
  `DATABASE` settings block. Give a measurement command using
  `pg_stat_activity` to inspect connection counts during an
  export. Recommend pool size = max-concurrent + 4 headroom.
- Cross-link from `docs/frictions/10`.

**Testing:** dry-run the guide against the test source stack with
the suggested settings, measure two `nbsnap export` runs at
default and tuned values, confirm the timing direction matches
the guide's prediction.

**Estimated Effort:** 1-2h

### [DOC-02b] Performance guide, front-proxy tuning sections

**Context:** when NetBox sits behind nginx or another WAF, the
proxy's rate-limit and body-size caps shape what nbsnap can
push through. This window covers the proxy-side levers.

**Requirements:**

- Extend `docs/operator-performance.md` with a "Front-proxy
  tuning" section.
- Sub-section "nginx rate limits". Include a working
  `limit_req_zone` + `limit_req` excerpt that allows nbsnap's
  retry-friendly burst pattern (500 reqs per 30 seconds, burst
  100). Reference RFC 9110 `Retry-After` semantics already
  honoured by `FEAT-01d`.
- Sub-section "Request and response body size caps". Recommend
  `client_max_body_size 32m` (covers the OpenAPI schema fetch
  which can run to several MB). Recommend
  `proxy_read_timeout 60s` for slower NetBox responses.
- Sub-section "Concurrency limits". Document
  `limit_conn_zone` + `limit_conn` and the interaction with
  `--max-concurrent`.
- Each sub-section names a curl or `nginx -T` command for the
  operator to inspect the live config.

**Testing:** apply one nginx rate-limit excerpt to a test proxy
in front of the source stack, confirm a `nbsnap export` retry
schedule survives the limit. Confirm `Retry-After` lands and
the export completes.

**Estimated Effort:** 1-2h

### [DOC-02c] Performance guide, GraphQL and bulk endpoint decision criteria

**Context:** the tool can opt into GraphQL (`RES-06`) and bulk
endpoints (`RES-07`) for specific read and write paths. The
guide names the trigger conditions so the operator knows when
to flip the flag.

**Requirements:**

- Extend `docs/operator-performance.md` with a "When to use
  GraphQL" section. Document the >30 percent wall-time gain
  threshold from `RES-06`. Cross-link to
  `docs/implementation/08-graphql-benchmark.md` for the
  measurement methodology. Name the two endpoints the gain
  is expected on (`dcim/devices/`, `ipam/ip-addresses/`).
- Extend with a "When to use bulk endpoints" section. Document
  the per-record error-handling cost vs throughput trade-off
  from `RES-07`. Name the two opt-in candidates,
  `--bulk-endpoints cables,interfaces`. Recommend the
  measurement, `time nbsnap import` with and without the
  flag on the `TEST-09a` 50k fixture.
- Add a "Decision flow" diagram (ASCII art) showing the order
  operators should evaluate, NetBox-side first, proxy second,
  GraphQL third, bulk fourth.

**Testing:** self-review confirms the decision flow matches the
RES-06 / RES-07 decision rules. Run a benchmark for the
GraphQL and bulk recommendations against the test stack,
confirm the numbers in the guide reflect a real measurement
not a guess.

**Estimated Effort:** 1-2h

### [DOC-03] Implementation notes index

**Context:** `docs/implementation/` carries per-decision rationales.
The index makes them findable.

**Requirements:**

- Create `docs/implementation/00-INDEX.md`.
- One line per implementation note linking to its file with a
  one-sentence summary.
- Cross-link from `docs/INDEX.md`.

**Testing:** click every link in the index, confirm targets exist.
Confirm every `docs/implementation/*.md` file is in the index.

**Estimated Effort:** 1-2h


---

## Future considerations

## Cut, ticket no longer planned

These tickets were dropped during the question-burndown enrichment
pass. Listed for traceability so the cross-references in
`PLAN.md` and the design docs can be cleaned up in a follow-up.

## Completed

Per the audit on 2026-06-15, every ticket whose code has shipped
has been removed from the open backlog. Git history is the
authoritative implementation record. `git log --oneline TODO.md`
shows the audit commit and every prior body update; the matching
feat/fix/test commits in `src/` and `tests/` carry the
implementation detail per ticket.
