# TODO

Outstanding work to deliver the NetBox portable-snapshot tool
(`nbsnap`). The phasing comes from `PLAN.md`. Every open entry is sized
for a 1, 2 hour focused work window. Each entry includes the file or
area it touches, the technical context the implementer needs, the
requirements as a concrete change list, and a testing step. Closed
items are removed per the CLAUDE.md hygiene rule, git history is the
authoritative record.

ID conventions:

* `INFRA-nn` for repo, CI, dev environment, test stack work.
* `RES-nn` for research and decision tickets that gate downstream
  implementation.
* `FEAT-nn` for feature implementation.
* `TEST-nn` for testing work that is not a side effect of a `FEAT-`.
* `DOC-nn` for documentation deliverables.
* `BUG-nn` for bug fixes.
* `REL-nn` for release and milestone gates.
* `SEC-nn` for security findings.
* `ARCH-nn` for architectural refactors.

Sub-tickets carry a lowercase letter suffix on the parent ID, for
example `ARCH-02h`, so a cross-reference from `PLAN.md` to the parent
concept still resolves.

Cross-references:

* `PLAN.md` for phase definitions and exit criteria.
* `docs/` for design documents.
* `docs/audits/20260616-architectural-and-security-audit.md` for the
  full audit narrative behind the `ARCH-*` and `SEC-*` parents below.
* `docs/frictions/` for friction-area deep-dives.
* `goals.md` for scope and success criteria.

---

## Codebase status

Phases 0 through 9 are implemented and committed. The
2026-06-16 architectural and security audit retired most of the
open backlog: ARCH-01 (`snapshot/` package), ARCH-04 (plugin
loader), ARCH-05 (`ContentType` value object), ARCH-07
(`requests` containment), ARCH-09 (resolver record context),
ARCH-10 (shared CLI flags), ARCH-11 (programmatic API), SEC-03
(cross-host redirect refusal), SEC-04 (manifest stores
`source_url_hash` only), and SEC-05 (audit log scrubs response
bodies) have all shipped, see `git log --grep='ARCH-0[14579]\|ARCH-1[01]\|SEC-0[345]'`.

The 2026-06-20 audit kept four areas open: the residual
`driver.py` slim-down (ARCH-02h/i), the bulk and parallelism
work (ARCH-03), the typed-models migration (ARCH-06), and the
import-side silent `CONTENT_TYPE_FILES` fallback (ARCH-08, now
narrowed to two call sites). Plus the operator-deferred BUG-09,
BUG-11, BUG-12 that need source-side action.

---

## Implementation hurdles (read before picking up a sub-ticket)

These notes are anchors that did not fit cleanly into any single sub-ticket. They survive across contexts so the next implementer does not re-discover them the hard way.

* **ARCH-02h is the gating item for ARCH-03.** The current `phase1_runner.py` and `field_resolver.py` are audit-documented scaffolds, the working bodies still live in `driver.py`. ARCH-03c (bulk in Phase 1) cannot land cleanly until the Phase-1 loop is actually inside `phase1_runner.run_phase1`, not a `NotImplementedError` placeholder. Sequence ARCH-02h before any ARCH-03 sub-ticket.
* **ARCH-03d (bulk partial-failure response shape) depends on the real NetBox 400 envelope.** Do not guess the schema, record one live response against `netbox.i.louie.se` (a deliberately malformed bulk POST is enough) and fixture it under `tests/fixtures/http/` before writing the parser.
* **ARCH-06a requires `datamodel-code-generator` in the `dev` extra.** Pin the generator version in `pyproject.toml` as part of the sub-ticket, the generated output is sensitive to the generator's defaults, an unpinned version makes ARCH-06b's drift check fail intermittently.

---

## Open

### ARCH-02: Reduce `driver.py` to a thin orchestrator

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-02`. Sub-tickets ARCH-02a..g shipped (the `ResolveContext`, the `lookahead` extraction, and the three scaffold modules `phase1_runner.py`, `phase2_runner.py`, `field_resolver.py`). ARCH-02h and ARCH-02i are the remaining surgical pieces.

#### ARCH-02h: Move the Phase-1 loop body out of `driver.py`

* **Context.** `wc -l src/nbsnap/import_/driver.py` reports 1537 lines today (target: <300). The three runner modules added by ARCH-02e..g are scaffolds, `phase1_runner.run_phase1` raises `NotImplementedError`, `phase2_runner.py` is a 21-line re-export of `phase2.py`, `field_resolver.py` re-exports the four `_resolve_*` helpers from `driver.py`. The working code still lives in `driver.run_import`'s closure. This ticket moves the body.
* **Files.**
  * `src/nbsnap/import_/driver.py` (the `run_import` function and the local helpers it carries).
  * `src/nbsnap/import_/phase1_runner.py` (becomes the real owner of the per-content-type loop).
  * `src/nbsnap/import_/field_resolver.py` (becomes the real owner of `_resolve_body`, `_resolve_body_via_ctx`, `_resolve_polymorphic_id_pairs`, `_resolve_termination_lists`, `_safe_resolve_m2m`).
  * `tests/unit/import_/test_phase1_runner.py` and friends (see ARCH-02i).
* **Requirements.**
  * Lift the Phase-1 loop body from `driver.run_import` into `phase1_runner.run_phase1`. The signature `run_phase1(plan_order: list[str], ctx: ResolveContext) -> None` is already documented; preserve it.
  * Lift the four `_resolve_*` helper bodies from `driver.py` into `field_resolver.py`. Re-export aliases from `driver.py` for the duration of one commit, then drop them in a follow-up.
  * After the move, `driver.run_import` should be the orchestration sequence only: build `ResolveContext`, run preflight, call `phase1_runner.run_phase1`, call `phase2_runner.run_phase2`, render summary.
  * `wc -l src/nbsnap/import_/driver.py` returns under 300.
* **Testing.** Existing integration tests (`tests/integration/test_import_*.py`, the renderer-parity tests) pass unchanged. `pytest tests/unit/import_/test_phase1_runner.py` exercises the lifted body, not the scaffold's `NotImplementedError`.
* **Estimated effort.** 4h (the loop closes over `ctx`, the body has to thread it explicitly).

#### ARCH-02i: Backfill regression tests for the runner modules

* **Context.** After ARCH-02h lands, the runner modules carry the real bodies but the unit tests are still the audit-shaped placeholders (`tests/unit/import_/test_phase1_runner.py:19` is a single import smoke test). The next refactor needs anchors.
* **Files.**
  * `tests/unit/import_/test_phase1_runner.py` (replace the import-only test).
  * `tests/unit/import_/test_phase2_runner.py` (replace the export-shape test).
  * `tests/unit/import_/test_field_resolver.py` (extend beyond aliases).
* **Requirements.**
  * For each module, add one happy-path test (a small snapshot dir, the function runs end-to-end against a recorded transport, the auditor sees the expected CREATED rows) and one failure-path test (a row whose FK does not resolve produces a SKIPPED audit row, no exception escapes).
  * Aim for >85% line coverage on each module (`pytest --cov=src/nbsnap/import_/phase1_runner --cov=src/nbsnap/import_/phase2_runner --cov=src/nbsnap/import_/field_resolver`).
* **Testing.** `pytest tests/unit/import_/ -q` stays green, coverage report shows the threshold.
* **Estimated effort.** 2h.

### ARCH-03: Bulk endpoints and bounded parallelism

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-03`. Blocked on ARCH-02h, the Phase 1 and Phase 2 loops have to be cleanly extracted into the runner modules before they can be batched or parallelised. Sub-tickets ARCH-03a..g.

#### ARCH-03a: Add `HTTPClient.post_bulk`

* **Context.** `src/nbsnap/http/client.py` has no bulk method today, every record is its own POST.
* **Requirements.**
  * Add `post_bulk(self, endpoint: str, payloads: list[dict[str, Any]], batch_size: int = 100) -> list[dict[str, Any]]`.
  * Slice `payloads` into `batch_size` chunks, POST each as a JSON list, flatten responses.
  * Preserve the existing retry/backoff and the source-write guard in `http/guard.py`.
* **Testing.** Add `tests/unit/test_http_client_bulk.py` with a mocked transport, asserting: a 250-record payload splits into three POSTs with `batch_size=100`; the response list preserves order.
* **Estimated effort.** 2h.

#### ARCH-03b: Wire `--bulk-batch-size` into `import_cli.py`

* **Context.** The bulk path needs an operator override and an environment variable fallback.
* **Requirements.**
  * Add `--bulk-batch-size INT` (default 100) in `src/nbsnap/import_cli.py`. Read fallback from `NBSNAP_BULK_BATCH_SIZE`.
  * Thread the value into `run_import` and on into `phase1_runner.run_phase1`.
* **Testing.** Add `tests/unit/test_import_cli_flags.py::test_bulk_batch_size_default_100` and `::test_bulk_batch_size_env_override` (uses `monkeypatch.setenv`).
* **Estimated effort.** 1h.

#### ARCH-03c: Switch `phase1_runner` to `post_bulk`

* **Context.** Phase 1 groups records by content type already, the batching boundary is free. Requires ARCH-02h and ARCH-03a both landed.
* **Requirements.**
  * Replace the per-record POST loop in `phase1_runner.run_phase1` with a call to `http.post_bulk` per content type.
  * Map each response item back to the source record so the auditor still emits one CREATED row per record.
* **Testing.** Extend `tests/unit/import_/test_phase1_runner.py` with a 30-record content type and assert one bulk POST plus 30 audit CREATED rows.
* **Estimated effort.** 2h.

#### ARCH-03d: Handle bulk partial-failure response shape

* **Context.** NetBox returns HTTP 400 with a list of per-row errors when one row in the batch fails. The exact envelope shape needs a recorded fixture, see the implementation hurdles note.
* **Requirements.**
  * Update `post_bulk` to detect the partial-failure shape and raise `BulkPartialFailure(successes, failures)`.
  * In `phase1_runner`, catch `BulkPartialFailure`, mark successful rows CREATED, route failed rows to the auditor with the per-row error.
* **Testing.** Add `tests/unit/test_http_client_bulk.py::test_partial_failure` using a fixture that returns the recorded partial-failure shape. Confirm successes still land and failures are audited.
* **Estimated effort.** 2h.

#### ARCH-03e: Add `import_/parallel.py` wrapper

* **Context.** Per-record PATCH in Phase 2 cannot use bulk, bounded parallelism is the next lever.
* **Requirements.**
  * Add `src/nbsnap/import_/parallel.py` exposing `run_bounded(fn, items, workers=4) -> list[Result]`.
  * Use `concurrent.futures.ThreadPoolExecutor` with a context manager and explicit shutdown.
* **Testing.** Add `tests/unit/import_/test_parallel.py` covering: empty input returns empty; 20 items with `workers=4` finish; an item that raises is captured in the `Result` rather than crashing the pool.
* **Estimated effort.** 1.5h.

#### ARCH-03f: Apply `run_bounded` to Phase 2

* **Context.** Phase 2 PATCH loop in `phase2.run_phase2` is sequential.
* **Requirements.**
  * Use `run_bounded` to issue PATCH calls in parallel within a content type.
  * Add `--phase2-workers INT` (default 4) to `src/nbsnap/import_cli.py`, env `NBSNAP_PHASE2_WORKERS`.
  * Preserve auditor ordering by sorting results by NK before emitting audit rows.
* **Testing.** Extend `tests/unit/import_/test_phase2_runner.py` with a 50-record deferred map and `workers=4`; assert all 50 audit rows emit and total HTTP-call count is 50.
* **Estimated effort.** 2h.

#### ARCH-03g: Throughput regression test

* **Context.** A timing-based test gives ARCH-03 a defensive floor.
* **Requirements.**
  * Add `tests/integration/test_import_throughput.py` that constructs a 500-record content type, runs the import against a mocked transport with 10 ms per HTTP call, and asserts wall-clock under 5 s.
  * Mark `@pytest.mark.slow` so default runs skip it.
* **Testing.** `pytest tests/integration/test_import_throughput.py -m slow`. Pass on a clean checkout.
* **Estimated effort.** 2h.

### ARCH-06: Pydantic v2 models from the OpenAPI schema

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-06`. ARCH-01 and ARCH-05 (the previous blockers) are both shipped, so this is now unblocked. Sub-tickets ARCH-06a..j stage the migration without breaking the wire boundary.

#### ARCH-06a: Generate the models module

* **Context.** `src/nbsnap/schema/openapi.py` already parses the schema (613 lines). The generated dataclasses live elsewhere.
* **Requirements.**
  * Add `scripts/generate_models.py` that runs `datamodel-code-generator` over the bundled OpenAPI schema and writes `src/nbsnap/schema/generated_models.py`.
  * Commit the generated file. Pin the generator version in `pyproject.toml` under the existing `dev` extra (line ~61-79).
* **Testing.** Add `tests/unit/schema/test_generated_models_import.py` that imports `nbsnap.schema.generated_models` and asserts `Device`, `Interface`, `IPAddress`, `Cable` classes exist.
* **Estimated effort.** 2h.

#### ARCH-06b: CI drift check

* **Context.** The generated file must not diverge from the schema.
* **Requirements.**
  * Add `tests/unit/schema/test_generated_models_drift.py` that regenerates into a `tempfile` and `diff`s against the committed file. Fail if non-empty.
  * Skip the test if the generator is not installed (mark as `slow` and document the dev-extra requirement).
* **Testing.** Locally edit `generated_models.py` and confirm the drift test fails; revert.
* **Estimated effort.** 1.5h.

#### ARCH-06c: Adopt Device and Interface at the export writer boundary

* **Context.** `src/nbsnap/export/writer.py` writes raw dicts today.
* **Requirements.**
  * In `export/writer.py`, parse each Device and Interface response through the generated models before writing JSON.
  * Catch `ValidationError` and re-raise as `SchemaDriftError(content_type, field, message)`. Define `SchemaDriftError` in `src/nbsnap/schema/__init__.py` (new exception).
* **Testing.** Add `tests/unit/export/test_writer_typed_device.py` covering happy path plus a schema-drift case (extra field accepted by `config_extra="allow"`, missing required field rejected).
* **Estimated effort.** 2h.

#### ARCH-06d: Adopt IPAddress, Prefix, IPRange at the export writer boundary

* **Requirements.**
  * Update `export/writer.py` for `ipam.ipaddress`, `ipam.prefix`, `ipam.iprange`.
* **Testing.** Add `tests/unit/export/test_writer_typed_ipam.py`.
* **Estimated effort.** 2h.

#### ARCH-06e: Adopt Cable, Site, Location, Rack at the export writer boundary

* **Requirements.**
  * Update `export/writer.py` for `dcim.cable`, `dcim.site`, `dcim.location`, `dcim.rack`.
* **Testing.** Add `tests/unit/export/test_writer_typed_dcim.py`. Run a full export against a recorded cassette to confirm parity with previous output.
* **Estimated effort.** 2h.

#### ARCH-06f: Adopt models at the import upsert boundary

* **Context.** Mirror the export migration on the import side.
* **Requirements.**
  * In `src/nbsnap/import_/upsert.py`, parse incoming snapshot rows through the generated models before HTTP submission.
  * Surface `ValidationError` as `SchemaDriftError`.
* **Testing.** Add `tests/unit/import_/test_upsert_typed.py` covering one happy and one drift case per content type touched in ARCH-06c..e.
* **Estimated effort.** 2h.

#### ARCH-06g: Migrate `snapshot_index` and `natkey/resolver` to typed records

* **Context.** Two internal layers still see `dict[str, Any]`.
* **Requirements.**
  * Change `SnapshotIndex._by_key` value type to the appropriate generated model.
  * Update `src/nbsnap/natkey/resolver.py` to accept the model and read attributes.
* **Testing.** `pytest tests/unit/natkey tests/unit/test_import_snapshot_index.py`.
* **Estimated effort.** 2h.

#### ARCH-06h: Migrate `fk_resolve.py` to typed parameters

* **Context.** `src/nbsnap/import_/fk_resolve.py` accepts and returns `Any` at the public surface.
* **Requirements.**
  * Replace `Any` with the generated base class and the relevant subclass union.
  * Add explicit return types.
* **Testing.** `pytest tests/unit/import_/test_fk_resolve.py`. `mypy --strict src/nbsnap/import_/fk_resolve.py` is clean.
* **Estimated effort.** 1.5h.

#### ARCH-06i: Tighten the type checker on the typed surface

* **Requirements.**
  * Add `[tool.mypy]` overrides for the typed modules to set `strict = true`.
  * Fix remaining mypy errors.
* **Testing.** `mypy src/nbsnap/` reports zero errors on the migrated modules.
* **Estimated effort.** 2h.

#### ARCH-06j: End-to-end typed integration test

* **Requirements.**
  * Add `tests/integration/test_typed_roundtrip.py` exporting a 200-record fixture, round-tripping through import, and asserting the import side observes typed models throughout.
* **Testing.** `pytest tests/integration/test_typed_roundtrip.py`.
* **Estimated effort.** 2h.

### ARCH-08: Close the two remaining silent `CONTENT_TYPE_FILES` fallbacks

* **Status.** Reframed by the 2026-06-20 audit. The original ticket framed this as a single change, but the audit found that ARCH-08a (rename `relative_path`) and ARCH-08b (`UnknownContentTypeError` in `snapshot/layout.py`) already shipped, see `src/nbsnap/snapshot/layout.py:49-79`. Two silent `.get(ct, fallback)` call sites remain on the import side and need the same fail-loud treatment.
* **Files.**
  * `src/nbsnap/import_/driver.py:267` (`file_path = snapshot_dir / CONTENT_TYPE_FILES.get(ct, f"{ct.replace('.', '/')}.jsonl")` — silently invents a path).
  * `src/nbsnap/import_/snapshot_index.py:78-93` (the docstring concedes "(content types we do not know about because they have no entry in `CONTENT_TYPE_FILES`) are also skipped silently").
* **Requirements.**
  * Replace the `driver.py:267` `.get(...)` with `relative_path(ct)` from `snapshot/layout.py`. An unknown content type at import time is a snapshot/destination contract violation, not a recoverable condition, raise `UnknownContentTypeError` and let it propagate to the CLI's existing error handler.
  * In `snapshot_index.py`, replace the silent skip with a warning logged via `nbsnap.log` (so operators see the divergence) and, when `--strict` is set on the import CLI, escalate to `UnknownContentTypeError`. Wire the new flag in `src/nbsnap/import_cli.py`.
  * Update both docstrings to describe the new fail-loud / warn-loud posture.
* **Testing.** Add `tests/unit/import_/test_unknown_content_type.py` with three cases: (1) driver path raises `UnknownContentTypeError` on an unknown ct; (2) `SnapshotIndex` logs a warning on an unknown directory entry; (3) `--strict` flag promotes the warning to an exception. Run the full integration suite to confirm no real snapshot exercises the old fallback.
* **Estimated effort.** 2h.

### INFRA-04: Periodic review of `tool.mypy.overrides` for typed deps

* **Context.** Surfaced by `__doc/code_reviews/20260618-1315_ci_lint_remediation.md`. `pyproject.toml:134` carries an `ignore_missing_imports = true` override for `zstandard`, `requests`, and `urllib3` because their typed surface either does not exist or collapses to `Any` under strict mypy. The override is an escape hatch, not a permanent state. Once any of these libraries (or their `types-*` companion packages) ships richer stubs, we should remove the corresponding entry so we get the type signal back at our call sites.
* **Files.**
  * `pyproject.toml` (the `[[tool.mypy.overrides]]` block under "Third-party deps without typed releases on PyPI").
* **Requirements.**
  * Once a quarter, run `mypy --strict src/` with the override block commented out and inspect which modules genuinely still need the escape hatch.
  * For modules that now ship usable stubs, remove the entry. Resolve any new errors at the call sites (a `cast` or a typed wrapper, not a per-line `# type: ignore`).
  * Leave a brief note in this ticket each time it is reviewed so we can see drift over time.
* **Testing.** `mypy --strict src/` exits cleanly without the override entry for each module that has been declared graduated.
* **Estimated effort.** 30 minutes per cycle.
* **Review log.**
  * 2026-06-19, initial entry, all three deps still need the override.

---

## Operator-deferred (no coding work)

These tickets live at the operator boundary, not in the codebase. They sit here as the audit trail; their resolution depends on access to the source NetBox or to the destination operator workflow.

### BUG-09: Frozen snapshot pre-dates enum-dict elimination (FEAT-36-blocker)

**Status.** DEFERRED, operator-domain, waiting on source reachability (ETA ~1 to 2 weeks from 2026-06-16). No code change closes this; the loop's invariant ("the frozen snapshot is read-only on disk") prevents an in-place rewrite.

**Context.** `/workspace/snapshot-source-frozen/` was exported before the import-side enum-dict elimination landed. Running `nbsnap import` without `--allow-enum-dict-bypass` aborts on the preflight; with the bypass, 12 files / 11 distinct fields coerce 5736 rows on the way in (see any of `tmp/nbsnap-rescue-{11,12,13}/import-attempt-*.log`, summary block `enum-dict bypass active: 12 files used the import-side coerce`).

Affected fields (file → field):
`dcim/cables.jsonl:status`, `dcim/device-types.jsonl:weight_unit`, `dcim/devices.jsonl:airflow`, `dcim/interfaces.jsonl:type`, `dcim/interfaces.jsonl:mode`, `dcim/locations.jsonl:status`, `dcim/racks.jsonl:status`, `dcim/sites.jsonl:status`, `extras/custom-fields.jsonl:filter_logic`, `ipam/ip-addresses.jsonl:status`, `ipam/ip-ranges.jsonl:status`.

**Operator remediation path.**

1. When source comes back, refresh the frozen snapshot via the `/nbsnap-export` skill (read-only GET against source). The new tree replaces `/workspace/snapshot-source-frozen/` as one explicit operator action.
2. Run a rescue iteration **without** `--allow-enum-dict-bypass` and confirm the preflight no longer blocks. If it still blocks, the export side did not strip the enum-dict shape, file a follow-up against the export path.
3. Once step 2 passes, drop the bypass flag from the rescue-loop's documented invocation and close this ticket.

### BUG-11: 86 `ipam.iprange` rows refused as overlap

**Status.** DEFERRED, source-domain only.

**Root cause confirmed by rescue-13.** NetBox's IPRange model carries an always-on overlap check in `IPRange.clean()` for ranges in the same VRF / global table. It is **not** gated by `ENFORCE_GLOBAL_UNIQUE` (the NetBox config docs at `configuration/miscellaneous.md` are explicit that the setting covers only "prefixes and IP addresses"). Rescue-13 confirmed this empirically: with `ENFORCE_GLOBAL_UNIQUE = False` on the destination, all 86 ipranges were still refused with the same overlap text. There is no destination-side toggle that clears these.

**Per-row NKs.** Available in any rescue-13 audit row matching `category=skipped`, `child.content_type=ipam.iprange`. Sample:
```bash
jq -c 'select(.category=="skipped" and .child.content_type=="ipam.iprange") | .child.nk' \
  tmp/nbsnap-rescue-13/audit.jsonl
```
All 86 cluster in the `92.33.40.x/26` and `92.33.4x.x/26` ranges, the participant pool space.

**Operator remediation path** (waiting on source reachability).

1. Walk the audit list above; for each pair, decide whether the overlap is intentional (kea pool overlap pattern) or stale (data debt).
2. **If intentional**: there is no clean destination-side fix. The realistic options are (a) assign one of each overlapping pair to a non-global VRF on the source so they no longer share an address space (NetBox honors VRF isolation), or (b) accept the destination cannot mirror these ranges and document the gap in the renderer's output.
3. **If stale**: delete the overlapping row at source.
4. Re-run the rescue loop after source-side changes propagate into a fresh snapshot via `/nbsnap-export`.

**Tool-side correctness** (already shipped): BUG-13 emits per-row SKIPPED audit lines; BUG-14 corrected the misleading skip-reason text that previously claimed `ENFORCE_GLOBAL_UNIQUE` would help.

### BUG-12: 4 `dcim.cable` rows skipped: at least one termination did not import

**Status.** DEFERRED, operator-domain, waiting on source reachability. Per-row visibility was delivered by BUG-13; regression coverage at the upsert layer already exists in `tests/unit/test_import_skipped_incomplete.py` and `tests/unit/test_import_cable_terminations.py`. No code change closes this ticket.

**Root cause (confirmed via frozen snapshot inspection).** Four cables in `/workspace/snapshot-source-frozen/dcim/cables.jsonl` carry **empty `a_terminations`** but a valid B-termination. The pattern strongly indicates a partial cable-delete on the source: the operator removed the A-side termination from the cable but the cable row itself was never deleted. NetBox would not normally let a cable exist without both terminations; this is data debt that bypassed the model's invariants somehow (older NetBox version, direct DB edit, or migration artifact).

**Concrete NKs.** All four B-terminations land on `C-ESPORTS-CITY-2-SW`:

| Cable A side | Cable B side |
| :--- | :--- |
| (empty) | `dcim.interface` `((('c',), 'C-ESPORTS-CITY-2-SW'), 'ge-0/0/8')` |
| (empty) | `dcim.interface` `((('c',), 'C-ESPORTS-CITY-2-SW'), 'ge-0/0/9')` |
| (empty) | `dcim.interface` `((('c',), 'C-ESPORTS-CITY-2-SW'), 'ge-0/0/10')` |
| (empty) | `dcim.interface` `((('c',), 'C-ESPORTS-CITY-2-SW'), 'ge-0/0/11')` |

Confirmed in rescue-12 and rescue-13 audit.jsonl files. The four cables are **not** present on the destination (the import correctly refused them), so there is nothing to clean up on the destination side.

**Operator remediation path** (waiting on source reachability).

1. In the source NetBox UI, navigate to **Devices → C-ESPORTS-CITY-2-SW → Interfaces** and look at `ge-0/0/8`, `ge-0/0/9`, `ge-0/0/10`, `ge-0/0/11`. Each should show a "Connected" cable with no other-end information.
2. For each, decide: delete the cable row (if the connection is genuinely gone) or restore the A-termination (if it was meant to stay).
3. Re-export the snapshot via `/nbsnap-export` after the cleanup; the next rescue iteration should land 0 cable skips.

---

## Future considerations

(none, see git history for the full implementation log)

## Completed

Per the audit on 2026-06-16 (and the follow-up audit on 2026-06-20), every ticket whose code has shipped has been removed from the open backlog. Git history is the authoritative implementation record. `git log --oneline TODO.md` shows the audit commits and every prior body update; the matching `feat`/`fix`/`test`/`refactor`/`docs` commits in `src/`, `tests/`, and `docs/` carry the implementation detail per ticket. To find the commit that closed a specific ticket, `git log --all --grep="<TICKET-ID>"`.
