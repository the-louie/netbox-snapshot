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
* `BUG-nn` for bug fixes.
* `REL-nn` for release and milestone gates.
* `SEC-nn` for security findings.
* `ARCH-nn` for architectural refactors.

Sub-tickets carry a lowercase letter suffix on the parent ID, for
example `ARCH-01a`, so a cross-reference from `PLAN.md` to the parent
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

Phases 0 through 9 are implemented and committed; environment state, frozen-snapshot vintage, and the rescue iteration log live in the `/workspace/.claude/skills/rescue-loop/SKILL.md` runbook and in git history (`git log --oneline --grep="^feat\|^fix\|^refactor\|^test\|^docs"`). The architectural and security audit of 2026-06-16 is preserved in `docs/audits/20260616-architectural-and-security-audit.md`; the decomposed sub-tickets it produced are listed below under the relevant `ARCH-*` and `SEC-*` parents.

---

## Implementation hurdles (read before picking up a sub-ticket)

These notes are anchors that did not fit cleanly into any single sub-ticket. They survive across contexts so the next implementer does not re-discover them the hard way.

* **ARCH-01e and ARCH-02d both touch `import_/driver.py` and `import_/lookahead.py`.** If both land in the same session the second one will inherit merge conflicts on the same call sites. Sequence ARCH-01 to completion (through ARCH-01g) before starting any ARCH-02 sub-ticket.
* **ARCH-03d (bulk partial-failure response shape) depends on the real NetBox 400 envelope.** Do not guess the schema; record one live response against `netbox.i.louie.se` (a deliberately malformed bulk POST is enough) and fixture it under `tests/fixtures/http/` before writing the parser.
* **ARCH-06a requires `datamodel-code-generator` in the `dev` extra.** Pin the generator version in `pyproject.toml` as part of the sub-ticket; the generated output is sensitive to the generator's defaults, an unpinned version makes ARCH-06b's drift check fail intermittently.
* **ARCH-07b is additive on top of existing 401/403 handling in `_request`.** The current branches are ad-hoc; the new `SnapshotAuthError` translation layers on top of them. Do **not** delete the old branches in the same sub-ticket, schedule that cleanup as a follow-up once ARCH-07c..e have proven the new exceptions reach the CLI.
* **SEC-03b is the only place in the redirect work where the dev has to make a follow-or-refuse call.** If uncertain, default to refusing the redirect and raising `SnapshotTransportError` with a clear cross-host message. A false refusal is recoverable; a leaked token is not.

---

## Open

### ARCH-01: Extract `snapshot/` package owning the data contract

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-01`. The goal is to make `export/` and `import_/` peers, each depending on a new `snapshot/` package that owns the manifest, the file layout, and the enum-dict coercion. Land sub-tickets in order, ARCH-01a through ARCH-01g.

#### ARCH-01b: Move `Manifest` and `MANIFEST_FILENAME` into `snapshot/manifest.py`

* **Context.** `Manifest` and `MANIFEST_FILENAME` live in `src/nbsnap/export/manifest.py` and are imported by `import_/driver.py:25-26` and `import_/preflight.py:26`.
* **Requirements.**
  * Move the `Manifest` dataclass and `MANIFEST_FILENAME` constant to `src/nbsnap/snapshot/manifest.py`.
  * Add re-exports in `src/nbsnap/export/manifest.py` (`from nbsnap.snapshot.manifest import Manifest, MANIFEST_FILENAME`) so existing call sites keep compiling during the migration window.
  * Update `src/nbsnap/snapshot/__init__.py::__all__` to include `Manifest`, `MANIFEST_FILENAME`.
* **Testing.** Copy `tests/unit/test_manifest.py` (or the existing test file covering `Manifest`) into `tests/unit/snapshot/test_manifest.py` and switch the import path. Run both old and new test files to confirm parity.
* **Estimated effort.** 1.5h.

#### ARCH-01c: Move `CONTENT_TYPE_FILES` and `relative_path` into `snapshot/layout.py`

* **Context.** `CONTENT_TYPE_FILES` is defined in `src/nbsnap/export/writer.py:26-47` with a silent fallback at line 52. Three importers in `import_/` re-import it.
* **Requirements.**
  * Move `CONTENT_TYPE_FILES` and `relative_path` to `src/nbsnap/snapshot/layout.py`.
  * Leave the silent fallback in place for this sub-ticket, ARCH-08a hardens it; do not break behaviour now.
  * Add re-exports in `src/nbsnap/export/writer.py` so existing imports continue to work.
  * Update `src/nbsnap/snapshot/__init__.py::__all__` to include `CONTENT_TYPE_FILES`, `relative_path`.
* **Testing.** Add `tests/unit/snapshot/test_layout.py` covering the existing behaviour: every key in `CONTENT_TYPE_FILES` maps to a `.jsonl` path under the documented prefix, and `relative_path("dcim.device")` returns the same path as `relative_path` did before the move (use the legacy import path as the reference).
* **Estimated effort.** 1.5h.

#### ARCH-01d: Promote `_collapse_enum_dict` to `snapshot.coerce.collapse_enum_dict`

* **Context.** `_collapse_enum_dict` lives in `src/nbsnap/export/extractor.py` and is re-imported by `import_/body_preparer.py:53` and `import_/upsert.py:575`. The leading underscore implies private but two packages depend on it.
* **Requirements.**
  * Move the function to `src/nbsnap/snapshot/coerce.py` as `collapse_enum_dict` (no underscore).
  * Leave a thin `_collapse_enum_dict = collapse_enum_dict` alias in `export/extractor.py` so existing imports keep working during the transition.
  * Update `src/nbsnap/snapshot/__init__.py::__all__` to include `collapse_enum_dict`.
* **Testing.** Copy any existing enum-dict tests under `tests/unit/snapshot/test_coerce.py` with the new import path. Add one test asserting `nbsnap.snapshot.collapse_enum_dict` and the legacy `nbsnap.export.extractor._collapse_enum_dict` are the same object.
* **Estimated effort.** 1h.

#### ARCH-01e: Switch `import_/` to `snapshot/` imports

* **Context.** Six imports point from `import_/` into `export/` today. Once the contracts live in `snapshot/`, every one of them moves over.
* **Requirements.**
  * Update the imports in `import_/driver.py:25-26`, `import_/preflight.py:26`, `import_/snapshot_index.py:91`, `import_/upsert.py:575`, `import_/body_preparer.py:53` to source `Manifest`, `MANIFEST_FILENAME`, `CONTENT_TYPE_FILES`, and `collapse_enum_dict` from `nbsnap.snapshot`.
  * Run `grep -RIn "from nbsnap.export" src/nbsnap/import_/` and ensure no hits remain that are not via the new aliases (they should all be removed in this ticket).
* **Testing.** Run `pytest tests/unit/import_ tests/integration -k "not slow"` and confirm no regressions. Add `tests/unit/test_import_no_export_imports.py` that walks `src/nbsnap/import_/` and asserts no `from nbsnap.export` line appears.
* **Estimated effort.** 2h.

#### ARCH-01f: Switch `export/` to `snapshot/` imports and remove the back-compat aliases

* **Context.** With `import_/` migrated, the temporary re-exports in `export/manifest.py`, `export/writer.py`, and `export/extractor.py` (added in ARCH-01b/c/d) are no longer needed.
* **Requirements.**
  * Update `export/driver.py`, `export/writer.py`, `export/manifest.py`, `export/extractor.py` to import from `nbsnap.snapshot` directly.
  * Remove the back-compat re-exports introduced in ARCH-01b/c/d.
* **Testing.** Run the full test suite, `pytest`. Confirm no test imports the removed legacy symbols.
* **Estimated effort.** 1.5h.

#### ARCH-01g: Enforce the layering invariant with a test

* **Context.** Once the migration is complete, a regression test prevents the layering violation from creeping back.
* **Requirements.**
  * Add `tests/unit/test_layering.py`. The test walks `src/nbsnap/snapshot/` and asserts no file imports from `nbsnap.export` or `nbsnap.import_`. It also walks `src/nbsnap/export/` and `src/nbsnap/import_/` and asserts neither imports from the other.
  * Use `ast` to parse imports, not regex, so re-aliased imports still count.
* **Testing.** Run `pytest tests/unit/test_layering.py`. Deliberately break it locally (add a stray `from nbsnap.export import Manifest` in `import_/driver.py`) and confirm it fails with a clear message; revert.
* **Estimated effort.** 1h.

### ARCH-02: Adopt `ResolveContext` and split `import_/driver.py`

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-02`. Sub-tickets ARCH-02a..i deliver the full refactor without touching public CLI behaviour.

#### ARCH-02a: Inventory the 15-parameter resolver call sites

* **Context.** `_resolve_body` at `import_/driver.py:407` carries nine keyword-only state buckets, and `lookahead.py:253-272` reconstructs the same bundle.
* **Requirements.**
  * Add `docs/audits/arch-02-resolver-inventory.md`. List every direct caller of `_resolve_body`, every helper that re-passes the same nine kwargs, and every test that constructs the bundle.
  * Cross-reference against the existing `import_/resolve_context.py` fields to highlight the delta to be added.
* **Testing.** No code change. Reviewer checks the inventory against `git grep "_resolve_body"`.
* **Estimated effort.** 1h.

#### ARCH-02b: Extend `ResolveContext` with the missing state fields

* **Context.** `import_/resolve_context.py` is the half-finished destination dataclass.
* **Requirements.**
  * Add the nine fields identified in ARCH-02a (`snapshot_index`, `processing_stack`, `deferred_queue`, `current_nk`, `auditor`, `failed_keys`, `deferred_fields_by_ct`, `warn_dedup`, `transient_keys`) to `ResolveContext` with explicit types.
  * Add a `ResolveContext.fresh()` classmethod that constructs a fully-initialised context for tests.
* **Testing.** Add `tests/unit/import_/test_resolve_context.py` covering field construction, `fresh()` defaults, and a check that all fields are mutable references (the existing code mutates them in place).
* **Estimated effort.** 2h.

#### ARCH-02c: Migrate `_resolve_body` to `(content_type, body, ctx)`

* **Context.** Once `ResolveContext` is complete, the signature can shrink.
* **Requirements.**
  * Reduce `_resolve_body(content_type, body, ctx)` in `driver.py`. Read every state field via `ctx.<field>`.
  * Update all internal call sites in `driver.py` to pass `ctx`.
* **Testing.** Run the import unit suite, `pytest tests/unit/import_`. The behaviour change should be zero; the signature change is mechanical.
* **Estimated effort.** 2h.

#### ARCH-02d: Migrate `lookahead.py` to `ResolveContext`

* **Context.** `lookahead.py:253-272` rebuilds the parameter bundle on the recursive callback path.
* **Requirements.**
  * Change the lookahead helpers to accept `ctx: ResolveContext` and pass it back into `_resolve_body`.
  * Delete the bundle-reconstruction code.
* **Testing.** Run `pytest tests/unit/import_/test_lookahead.py` plus `tests/integration/test_import_*`. Confirm cycle resolution still completes (`Device.primary_ip4` round-trip).
* **Estimated effort.** 1.5h.

#### ARCH-02e: Extract `phase1_runner.py`

* **Context.** The Phase 1 loop at `driver.py:265-290` orchestrates the per-content-type create pass.
* **Requirements.**
  * Move the Phase 1 loop and its helpers into `src/nbsnap/import_/phase1_runner.py`, exposed as `run_phase1(plan, ctx)`.
  * `driver.py` calls `run_phase1` and nothing else for Phase 1.
* **Testing.** Add `tests/unit/import_/test_phase1_runner.py` covering: empty plan returns without HTTP calls; a one-content-type plan posts once; auditor receives one CREATED row per record. Run the full integration suite for parity.
* **Estimated effort.** 2h.

#### ARCH-02f: Extract `phase2_runner.py`

* **Context.** Phase 2 patches the cycle-closing fields (`primary_ip4`, cable terminations).
* **Requirements.**
  * Move the Phase 2 patch loop and `deferred_fields_by_ct` handling into `src/nbsnap/import_/phase2_runner.py` as `run_phase2(plan, ctx)`.
  * Keep the per-field error budget in the runner, not the driver.
* **Testing.** Add `tests/unit/import_/test_phase2_runner.py`: empty deferred map skips; one deferred patch fires a PATCH; PATCH failure routes to auditor. Confirm `tests/integration/test_import_cycles.py` (or the closest existing analogue) still passes.
* **Estimated effort.** 2h.

#### ARCH-02g: Extract `field_resolver.py`

* **Context.** The four `_resolve_*` helpers handle FK, polymorphic FK, M2M, and termination-list fields.
* **Requirements.**
  * Move the four helpers to `src/nbsnap/import_/field_resolver.py` and import them into `driver.py`.
  * Keep the helpers as module-level functions taking `(content_type, body, ctx)`.
* **Testing.** Add `tests/unit/import_/test_field_resolver.py` with one test per helper using a faked `ResolveContext`. Confirm `pytest tests/unit/import_` is green.
* **Estimated effort.** 1.5h.

#### ARCH-02h: Reduce `driver.py` to a thin orchestrator

* **Context.** With Phase 1, Phase 2, and field resolution extracted, `driver.py` is largely orchestration glue.
* **Requirements.**
  * Trim `driver.py` to under 300 lines: imports, `run_import` top-level function, the orchestration sequence (preflight, plan, phase1, phase2, summary).
  * Move any helper that is not called from `run_import` into the appropriate runner module.
* **Testing.** `wc -l src/nbsnap/import_/driver.py` is below 300. The full test suite stays green.
* **Estimated effort.** 1.5h.

#### ARCH-02i: Backfill regression tests for the new modules

* **Context.** The refactor preserves behaviour but the new modules deserve targeted unit tests.
* **Requirements.**
  * Add coverage for the public entry points of `phase1_runner`, `phase2_runner`, `field_resolver` so the next refactor has anchors.
  * Aim for one happy-path and one failure-path test per module.
* **Testing.** `pytest tests/unit/import_/ --cov=src/nbsnap/import_/phase1_runner --cov=src/nbsnap/import_/phase2_runner --cov=src/nbsnap/import_/field_resolver` should show >85% line coverage on each.
* **Estimated effort.** 2h.

### ARCH-03: Bulk endpoints and bounded parallelism

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-03`. Depends on ARCH-02 because the Phase 1 and Phase 2 loops need to be cleanly extracted before they can be batched or parallelised. Sub-tickets ARCH-03a..g.

#### ARCH-03a: Add `HTTPClient.post_bulk`

* **Context.** `http/client.py` has no bulk method today; every record is its own POST.
* **Requirements.**
  * Add `post_bulk(self, endpoint: str, payloads: list[dict[str, Any]], batch_size: int = 100) -> list[dict[str, Any]]`.
  * Slice `payloads` into `batch_size` chunks; POST each as a JSON list; flatten responses.
  * Preserve the existing retry/backoff and source-write guard.
* **Testing.** Add `tests/unit/test_http_client_bulk.py` with a recorded-cassette or mocked transport, asserting: a 250-record payload splits into three POSTs with `batch_size=100`; the response list preserves order.
* **Estimated effort.** 2h.

#### ARCH-03b: Wire `--bulk-batch-size` into `import_cli.py`

* **Context.** The bulk path needs an operator override and an environment variable fallback.
* **Requirements.**
  * Add `--bulk-batch-size INT` (default 100) in `import_cli.py`. Read fallback from `NBSNAP_BULK_BATCH_SIZE`.
  * Thread the value into `run_import` and on into `phase1_runner`.
* **Testing.** Add `tests/unit/test_import_cli_flags.py::test_bulk_batch_size_default_100`. Add `tests/unit/test_import_cli_flags.py::test_bulk_batch_size_env_override` exercising `monkeypatch.setenv`.
* **Estimated effort.** 1h.

#### ARCH-03c: Switch `phase1_runner` to `post_bulk`

* **Context.** Phase 1 groups records by content type already; the batching boundary is free.
* **Requirements.**
  * Replace the per-record POST loop in `phase1_runner.run_phase1` with a call to `http.post_bulk` per content type.
  * Map each response item back to the source record so auditor still emits one CREATED row per record.
* **Testing.** Extend `tests/unit/import_/test_phase1_runner.py` with a 30-record content type and assert one bulk POST plus 30 audit CREATED rows.
* **Estimated effort.** 2h.

#### ARCH-03d: Handle bulk partial-failure response shape

* **Context.** NetBox returns HTTP 400 with a list of per-row errors when one row in the batch fails.
* **Requirements.**
  * Update `post_bulk` to detect the partial-failure shape and raise `BulkPartialFailure(successes, failures)`.
  * In `phase1_runner`, catch `BulkPartialFailure`, mark successful rows CREATED, route failed rows to the auditor with the per-row error.
* **Testing.** Add `tests/unit/test_http_client_bulk.py::test_partial_failure` using a cassette that returns the partial-failure shape. Confirm successes still land and failures are audited.
* **Estimated effort.** 2h.

#### ARCH-03e: Add `import_/parallel.py` wrapper

* **Context.** Per-record PATCH in Phase 2 cannot use bulk; bounded parallelism is the next lever.
* **Requirements.**
  * Add `src/nbsnap/import_/parallel.py` exposing `run_bounded(fn, items, workers=4) -> list[Result]`.
  * Use `concurrent.futures.ThreadPoolExecutor` with a context manager and explicit shutdown.
* **Testing.** Add `tests/unit/import_/test_parallel.py` covering: empty input returns empty; 20 items with `workers=4` finish; an item that raises is captured in the `Result` rather than crashing the pool.
* **Estimated effort.** 1.5h.

#### ARCH-03f: Apply `run_bounded` to Phase 2

* **Context.** Phase 2 PATCH loop in `phase2_runner` is sequential.
* **Requirements.**
  * Use `run_bounded` to issue PATCH calls in parallel within a content type.
  * Add `--phase2-workers INT` (default 4) to `import_cli.py`, env `NBSNAP_PHASE2_WORKERS`.
  * Preserve auditor ordering by sorting results by NK before emitting audit rows.
* **Testing.** Extend `tests/unit/import_/test_phase2_runner.py` with a 50-record deferred map and `workers=4`; assert all 50 audit rows emit and total HTTP-call count is 50.
* **Estimated effort.** 2h.

#### ARCH-03g: Throughput regression test

* **Context.** A timing-based test gives ARCH-03 a defensive floor.
* **Requirements.**
  * Add `tests/integration/test_import_throughput.py` that constructs a 500-record content type, runs the import against a mocked transport with 10 ms per HTTP call, and asserts wall-clock under 5 s.
  * Mark `@pytest.mark.slow` so default runs skip it.
* **Testing.** Run `pytest tests/integration/test_import_throughput.py -m slow`. Confirm pass on a clean checkout.
* **Estimated effort.** 2h.

### ARCH-04: Wire the plugin loader

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-04`.

#### ARCH-04a: Add `NKRegistry.with_plugins`

* **Context.** `plugins/api.py:68` defines `load_all(registry: NKRegistry)` but it is never called.
* **Requirements.**
  * Add a `NKRegistry.with_plugins(directory: Path | None) -> "NKRegistry"` classmethod that builds the default registry, then calls `plugins.api.load_all(registry, directory)`.
  * If `directory` is None, fall back to the value of `NBSNAP_PLUGINS_DIR`, then to a no-op (no plugins loaded).
* **Testing.** Add `tests/unit/natkey/test_registry_with_plugins.py`: no directory yields the default registry; a directory with one valid plugin yields default plus one extra entry; a directory with a malformed plugin file raises a clear `PluginLoadError`.
* **Estimated effort.** 1.5h.

#### ARCH-04b: Rewrite `sample_bgp.py` to use the public surface

* **Context.** `plugins/sample_bgp.py:21-39` pokes `NKSpec` and `NKField` directly, contradicting its job of being a canonical example.
* **Requirements.**
  * Rewrite the sample plugin to use `Registrar.add_nkspec` and `Registrar.add_field_rewriter` exclusively.
  * Add a module docstring explaining how a plugin is structured and where the loader picks it up.
* **Testing.** Add `tests/unit/plugins/test_sample_bgp.py` that loads `sample_bgp` through `Registrar` and asserts the resulting NKSpec is equivalent to the previous direct-poke version (compare field by field).
* **Estimated effort.** 1.5h.

#### ARCH-04c: Wire `--plugins-dir` into the CLIs

* **Context.** The factory exists once ARCH-04a lands; the CLIs need to opt in.
* **Requirements.**
  * Add `--plugins-dir PATH` to `import_cli.py` and `export_cli.py`. Route to `NKRegistry.with_plugins`.
  * Document the env-var fallback `NBSNAP_PLUGINS_DIR` in `--help`.
* **Testing.** Add `tests/unit/test_cli_plugins_flag.py` exercising the flag on both subcommands. Assert the registry passed into `run_import` / `run_export` carries the loaded plugin.
* **Estimated effort.** 1h.

#### ARCH-04d: Integration test loading `sample_bgp` end to end

* **Context.** A real end-to-end load proves the contract holds.
* **Requirements.**
  * Add `tests/integration/test_plugin_load_e2e.py`. The test runs `run_import` against a mocked NetBox with `--plugins-dir tests/fixtures/plugins/` where `sample_bgp.py` is staged.
  * Confirm the BGP-flavoured NK appears in audit output for at least one row.
* **Testing.** Run `pytest tests/integration/test_plugin_load_e2e.py`.
* **Estimated effort.** 1.5h.

### ARCH-05: Introduce a `ContentType` value object

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-05`. Depends on ARCH-01 so the new value object can live under `snapshot/` if a shared home makes sense, or under `schema/` if not.

#### ARCH-05a: Add the `ContentType` dataclass

* **Context.** No central place owns the `app.model` → endpoint mapping today.
* **Requirements.**
  * Add `src/nbsnap/schema/content_type.py` with `@dataclass(frozen=True) class ContentType: app: str; model: str`.
  * Add `ContentType.from_str("dcim.device")`, `ContentType.as_str() -> "dcim.device"`, and `ContentType.endpoint() -> "dcim/devices/"`.
  * Reject invalid input in `from_str` with a clear `InvalidContentTypeError`.
* **Testing.** Add `tests/unit/schema/test_content_type.py` covering round-trip, endpoint mapping for `dcim.device`/`ipam.iprange`/`dcim.cable`, and `InvalidContentTypeError` on `"dcim"` and `"dcim.devic"`.
* **Estimated effort.** 2h.

#### ARCH-05b: Centralise the endpoint table

* **Context.** `CONTENT_TYPE_ENDPOINTS` is in `natkey/verify.py:49-70` today.
* **Requirements.**
  * Move `CONTENT_TYPE_ENDPOINTS` to `schema/content_type.py` as a private `_ENDPOINTS` dict consulted by `ContentType.endpoint`.
  * Re-export the old name from `natkey/verify.py` for one ticket to avoid churn.
* **Testing.** Extend `tests/unit/schema/test_content_type.py` to assert every entry in `_ENDPOINTS` round-trips through `from_str`.
* **Estimated effort.** 1.5h.

#### ARCH-05c: Migrate `graph/polymorphic.py` to `ContentType`

* **Context.** `POLYMORPHIC_HINTS` and `KNOWN_VALIDATION_CYCLES` at `graph/polymorphic.py:152-268` hold bare strings.
* **Requirements.**
  * Replace the bare string keys with `ContentType` instances.
  * Update callers in `graph/algo.py` to pass `ContentType` rather than strings.
* **Testing.** Run `pytest tests/unit/graph`. Add one test asserting `POLYMORPHIC_HINTS` keys are all `ContentType` instances.
* **Estimated effort.** 2h.

#### ARCH-05d: Migrate `natkey/registry.py` to `ContentType`

* **Context.** Every `NKSpec` registration at `natkey/registry.py:30-127` uses bare strings.
* **Requirements.**
  * Update `NKSpec.content_type` and the registry's keying to use `ContentType`.
  * Migrate the 30+ registrations.
* **Testing.** Run the natkey unit suite. Add one test asserting `NKRegistry.get("dcim.device")` accepts both a `ContentType` and a string for backwards compatibility.
* **Estimated effort.** 2h.

#### ARCH-05e: Migrate `natkey/verify.py` callers

* **Context.** Verify reaches into the endpoint table.
* **Requirements.**
  * Replace local lookups in `natkey/verify.py` with `ContentType.endpoint`.
  * Drop the re-export shim introduced in ARCH-05b.
* **Testing.** Run `pytest tests/unit/natkey/test_verify.py`. Add a test exercising verify against a stub HTTP client to confirm the endpoint resolves the same way as before.
* **Estimated effort.** 1.5h.

#### ARCH-05f: Migrate `plan_cli.py` scope handling

* **Context.** `plan_cli.py` accepts bare schema strings as scope examples.
* **Requirements.**
  * Parse `--scope` values into `ContentType` and pass them through.
  * Reject unknown content types at parse time with `InvalidContentTypeError`.
* **Testing.** Add `tests/unit/test_plan_cli_scope.py` covering valid and invalid scope arguments.
* **Estimated effort.** 1h.

#### ARCH-05g: Regression test on typo detection

* **Context.** ARCH-05's payoff is catching typos like `dcim.devic` at parse time.
* **Requirements.**
  * Add `tests/unit/schema/test_content_type_typos.py` with a parametrised list of common typos and assert each raises `InvalidContentTypeError`.
* **Testing.** Run the new test; deliberately break by registering `dcim.devic` in `_ENDPOINTS` and confirm the test catches it; revert.
* **Estimated effort.** 1h.

### ARCH-06: Pydantic v2 models from the OpenAPI schema

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-06`. Depends on ARCH-01 and ARCH-05. Sub-tickets ARCH-06a..j stage the migration without breaking the wire boundary.

#### ARCH-06a: Generate the models module

* **Context.** `schema/openapi.py` already parses the schema (613 lines). The generated dataclasses live elsewhere.
* **Requirements.**
  * Add `scripts/generate_models.py` that runs `datamodel-code-generator` over the bundled OpenAPI schema and writes `src/nbsnap/schema/generated_models.py`.
  * Commit the generated file. Pin the generator version in `pyproject.toml` under a `dev` extra.
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

* **Context.** `export/writer.py` writes raw dicts today.
* **Requirements.**
  * In `export/writer.py`, parse each Device and Interface response through the generated models before writing JSON.
  * Catch `ValidationError` and re-raise as `SchemaDriftError(content_type, field, message)`.
* **Testing.** Add `tests/unit/export/test_writer_typed_device.py` covering happy path plus a schema-drift case (extra field accepted by config_extra="allow", missing required field rejected).
* **Estimated effort.** 2h.

#### ARCH-06d: Adopt IPAddress, Prefix, IPRange at the export writer boundary

* **Context.** Same migration shape as ARCH-06c.
* **Requirements.**
  * Update `export/writer.py` for `ipam.ipaddress`, `ipam.prefix`, `ipam.iprange`.
* **Testing.** Extend `tests/unit/export/test_writer_typed_ipam.py`.
* **Estimated effort.** 2h.

#### ARCH-06e: Adopt Cable, Site, Location, Rack at the export writer boundary

* **Context.** Final batch on the export side.
* **Requirements.**
  * Update `export/writer.py` for `dcim.cable`, `dcim.site`, `dcim.location`, `dcim.rack`.
* **Testing.** Extend `tests/unit/export/test_writer_typed_dcim.py`. Run a full export against a recorded cassette to confirm parity with previous output.
* **Estimated effort.** 2h.

#### ARCH-06f: Adopt models at the import upsert boundary

* **Context.** Mirror the export migration on the import side.
* **Requirements.**
  * In `import_/upsert.py`, parse incoming snapshot rows through the generated models before HTTP submission.
  * Surface `ValidationError` as `SchemaDriftError`.
* **Testing.** Add `tests/unit/import_/test_upsert_typed.py` covering one happy and one drift case per content type touched in ARCH-06c..e.
* **Estimated effort.** 2h.

#### ARCH-06g: Migrate `snapshot_index` and `natkey/resolver` to typed records

* **Context.** Two internal layers still see `dict[str, Any]`.
* **Requirements.**
  * Change `SnapshotIndex._by_key` value type to the appropriate generated model.
  * Update `natkey/resolver.py` to accept the model and read attributes.
* **Testing.** Run `pytest tests/unit/natkey tests/unit/import_/test_snapshot_index.py`.
* **Estimated effort.** 2h.

#### ARCH-06h: Migrate `fk_resolve.py` to typed parameters

* **Context.** `import_/fk_resolve.py:19-71` accepts and returns `Any`.
* **Requirements.**
  * Replace `Any` with `Model` (the generated base) and the relevant subclass union.
  * Add explicit return types.
* **Testing.** Run `pytest tests/unit/import_/test_fk_resolve.py`. Confirm the type stubs satisfy `mypy --strict src/nbsnap/import_/fk_resolve.py`.
* **Estimated effort.** 1.5h.

#### ARCH-06i: Tighten the type checker on the typed surface

* **Context.** Once the boundary is typed, mypy can enforce it.
* **Requirements.**
  * Add `[tool.mypy]` overrides for the typed modules to set `strict = true`.
  * Fix remaining mypy errors.
* **Testing.** Run `mypy src/nbsnap/` and confirm zero errors on the migrated modules.
* **Estimated effort.** 2h.

#### ARCH-06j: End-to-end typed integration test

* **Context.** A representative test proves export and import both run through the typed pipeline.
* **Requirements.**
  * Add `tests/integration/test_typed_roundtrip.py` exporting a 200-record fixture, round-tripping through import, and asserting the import side observes typed models throughout.
* **Testing.** Run `pytest tests/integration/test_typed_roundtrip.py`.
* **Estimated effort.** 2h.

### ARCH-07: Stop leaking `requests` library details out of `http/`

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-07`.

#### ARCH-07b: Translate `requests` exceptions inside the HTTP client

* **Context.** `http/client.py::_request` is the right place to wrap.
* **Requirements.**
  * Catch `requests.exceptions.SSLError` and re-raise as `SnapshotConnectivityError(reason="tls", base_url=...)`.
  * Catch `requests.exceptions.ConnectionError` and re-raise as `SnapshotConnectivityError(reason="connection", base_url=...)`.
  * Catch HTTP 401/403 and re-raise as `SnapshotAuthError`.
* **Testing.** Extend `tests/unit/test_http_client_transport.py` with one case per translation path.
* **Estimated effort.** 1.5h.

#### ARCH-07c: Replace `requests.exceptions` catches in `import_cli.py`

* **Context.** `import_cli.py:34, 249, 256` import `requests` for the catch.
* **Requirements.**
  * Remove `import requests` from `import_cli.py`.
  * Catch `SnapshotConnectivityError` and `SnapshotAuthError` for the operator-facing messages.
* **Testing.** Update `tests/unit/test_import_cli.py` to drive the connectivity/auth code paths through the new exceptions.
* **Estimated effort.** 1.5h.

#### ARCH-07d: Replace `NetboxHTTPError` catches outside `http/`

* **Context.** `graph/polymorphic.py:83`, `import_/phase2.py`, `schema/content_types.py` catch `NetboxHTTPError` outside the HTTP package.
* **Requirements.**
  * Decide per call site: either re-raise as a domain-appropriate exception, or move the catch into `http/` if it is truly a transport concern.
  * Document the choice inline (one-line comment per site).
* **Testing.** Run the full unit suite. Confirm no module outside `src/nbsnap/http/` references `NetboxHTTPError` or `requests.exceptions`.
* **Estimated effort.** 1.5h.

#### ARCH-07e: Enforce the boundary with a test

* **Context.** A regression test holds the line.
* **Requirements.**
  * Add `tests/unit/test_http_boundary.py` walking `src/nbsnap/` and asserting that no module outside `nbsnap.http` imports `requests`.
* **Testing.** Run the new test; deliberately add `import requests` to `import_cli.py` and confirm the test catches it; revert.
* **Estimated effort.** 1h.

### ARCH-08: Replace the silent `CONTENT_TYPE_FILES` fallback

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-08`. Depends on ARCH-01.

#### ARCH-08a: Hard-fail on unknown content type in `snapshot/layout.py`

* **Context.** `relative_path` falls back to `f"{ct.replace('.', '/')}.jsonl"` silently.
* **Requirements.**
  * Remove the fallback. Raise `UnknownContentTypeError(content_type)` if the key is not in `CONTENT_TYPE_FILES`.
  * Add the exception to `snapshot/layout.py`.
* **Testing.** Add `tests/unit/snapshot/test_layout_unknown.py` asserting the error is raised with a clear message.
* **Estimated effort.** 1.5h.

#### ARCH-08b: Preflight check in `import_/preflight.py`

* **Context.** A snapshot whose manifest carries an unknown content type should be rejected before any HTTP call.
* **Requirements.**
  * In `import_/preflight.py`, walk `manifest.content_types` and call `relative_path` on each.
  * Aggregate failures and report all unknown types in one error.
* **Testing.** Add `tests/unit/import_/test_preflight_unknown_ct.py` with a manifest carrying `dcim.devic` and assert the preflight refuses before reaching upsert.
* **Estimated effort.** 1.5h.

#### ARCH-08c: End-to-end test

* **Context.** Confirm the new error path renders sanely to the operator.
* **Requirements.**
  * Add `tests/integration/test_import_unknown_ct_message.py` running `nbsnap import` against a fixture with one unknown content type; assert exit code non-zero and stderr contains the content type name.
* **Testing.** Run the integration test.
* **Estimated effort.** 1h.

### ARCH-09: Record-level context on resolver exceptions

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-09`.

#### ARCH-09a: Add `ResolverFieldError` in `natkey/resolver.py`

* **Context.** `natkey/resolver.py:58-68` raises bare `ValueError`.
* **Requirements.**
  * Add `class ResolverFieldError(ValueError)` carrying `content_type`, `natural_key`, `field_name`, `hint`.
  * Override `__str__` to render `"[<ct> <nk>.<field>] <message> (hint: <hint>)"`.
* **Testing.** Add `tests/unit/natkey/test_resolver_errors.py::test_resolver_field_error_renders` covering the format string.
* **Estimated effort.** 1.5h.

#### ARCH-09b: Add `ResolverFKMissError` in `import_/fk_resolve.py`

* **Context.** `import_/fk_resolve.py:38` raises bare `KeyError`.
* **Requirements.**
  * Add `class ResolverFKMissError(KeyError)` carrying `content_type`, `natural_key`, `target_ct`, `hint`.
* **Testing.** Add `tests/unit/import_/test_fk_resolve_errors.py::test_fk_miss_error_renders`.
* **Estimated effort.** 1.5h.

#### ARCH-09c: Migrate raise sites

* **Context.** Every existing raise site under `natkey/resolver.py` and `import_/fk_resolve.py` switches to the new types.
* **Requirements.**
  * Update each raise site with concrete `content_type`, `natural_key`, `field_name`, `hint`.
  * Hints should call out the three most likely causes: missing source data, scope mismatch, schema skew.
* **Testing.** Run the natkey and import unit suites. Confirm log messages in `tests/integration/test_import_*` now include the row anchor.
* **Estimated effort.** 1.5h.

#### ARCH-09d: Auditor cross-reference test

* **Context.** Confirm the new exception fields propagate into audit rows.
* **Requirements.**
  * Add `tests/integration/test_audit_resolver_context.py` triggering one `ResolverFieldError` and one `ResolverFKMissError`. Assert the audit row carries `content_type`, `natural_key`, `field_name`.
* **Testing.** Run the integration test.
* **Estimated effort.** 1h.

### ARCH-10: Shared CLI flags

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-10`.

#### ARCH-10a: Create `cli/common.py` with shared flag builders

* **Context.** Today every subcommand re-declares TLS, scope, audit flags.
* **Requirements.**
  * Add `src/nbsnap/cli/common.py` with `add_tls_flags(parser)`, `add_scope_flags(parser)`, `add_audit_flags(parser)`.
  * Each function attaches the canonical flag name and help text.
* **Testing.** Add `tests/unit/cli/test_common_flags.py` asserting each helper adds the expected flags with the documented defaults.
* **Estimated effort.** 2h.

#### ARCH-10b: Migrate `export_cli.py`

* **Context.** Export currently declares its own TLS and scope flags.
* **Requirements.**
  * Replace the inline declarations with calls to `add_tls_flags(parser)` and `add_scope_flags(parser)`.
  * Keep any export-specific flags in `export_cli.py`.
* **Testing.** Add `tests/unit/cli/test_export_cli_flags.py` asserting the shared flags resolve identically across subcommands.
* **Estimated effort.** 1.5h.

#### ARCH-10c: Migrate `import_cli.py`

* **Context.** Largest surface, 15 flags at lines 66-179.
* **Requirements.**
  * Replace the inline declarations with `add_tls_flags`, `add_scope_flags`, `add_audit_flags`.
  * Keep import-specific flags inline.
* **Testing.** Add `tests/unit/cli/test_import_cli_flags.py` parity assertions.
* **Estimated effort.** 2h.

#### ARCH-10d: Migrate `reset_cli.py` and `plan_cli.py`

* **Context.** The two remaining subcommands.
* **Requirements.**
  * Apply the same migration to `reset_cli.py` and `plan_cli.py`.
* **Testing.** Extend `tests/unit/cli/` with parity tests for these subcommands.
* **Estimated effort.** 1.5h.

#### ARCH-10e: Canonicalise the scope flag name

* **Context.** Scope flag drifts between `--only`, `--content-types`, `--max-skipped-ct`.
* **Requirements.**
  * Pick `--content-types` as canonical. Add `--only` as a deprecated alias that emits a warning to stderr.
  * Document the deprecation in `--help` and in `docs/operator-runbook.md`.
* **Testing.** Add `tests/unit/cli/test_scope_alias_deprecation.py` asserting the alias still works and the warning fires.
* **Estimated effort.** 1.5h.

#### ARCH-10f: Help-text parity test

* **Context.** A regression test ensures the help strings stay aligned.
* **Requirements.**
  * Add `tests/unit/cli/test_help_parity.py` rendering `--help` for each subcommand and asserting the wording for `--no-verify-tls`, `--content-types`, `--audit-out` is identical.
* **Testing.** Run the new test.
* **Estimated effort.** 1h.

### ARCH-11: Programmatic API surface

Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#ARCH-11`.

#### ARCH-11a: Re-export from `export/__init__.py`

* **Context.** `run_export` and `Manifest` are not re-exported.
* **Requirements.**
  * Add `from .driver import run_export` and `from nbsnap.snapshot.manifest import Manifest` to `src/nbsnap/export/__init__.py`.
  * Define `__all__ = ["run_export", "Manifest"]`.
* **Testing.** Add `tests/unit/export/test_public_api.py` asserting `from nbsnap.export import run_export, Manifest` resolves.
* **Estimated effort.** 1h.

#### ARCH-11b: Re-export from `import_/__init__.py`

* **Context.** Import currently exports only `ResolveContext`.
* **Requirements.**
  * Add `from .driver import run_import` to `src/nbsnap/import_/__init__.py`.
  * Define `__all__ = ["run_import", "ResolveContext"]`.
* **Testing.** Add `tests/unit/import_/test_public_api.py` parallel to the export one.
* **Estimated effort.** 1h.

#### ARCH-11c: Re-export from top-level `nbsnap/__init__.py`

* **Context.** `nbsnap/__init__.py` only exposes `__version__`.
* **Requirements.**
  * Add `from nbsnap.export import run_export, Manifest` and `from nbsnap.import_ import run_import`.
  * Define `__all__ = ["__version__", "run_export", "run_import", "Manifest"]`.
* **Testing.** Add `tests/unit/test_top_level_api.py` asserting the four symbols import.
* **Estimated effort.** 1h.

#### ARCH-11d: Embedded-use integration test

* **Context.** Prove the public API is enough to run nbsnap from a script.
* **Requirements.**
  * Add `tests/integration/test_embedded_use.py` that imports only the top-level symbols and runs a small export-then-import cycle against mocked transports.
* **Testing.** Run the new test.
* **Estimated effort.** 1h.

### SEC-03: Bearer token follows redirects across hosts

Severity high. Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#SEC-03`.

#### SEC-03b: One-hop redirect helper with host validation

* **Context.** Some GET flows may legitimately need to follow a redirect.
* **Requirements.**
  * Add `_follow_one_safe_hop(response)` to `http/client.py`. It returns the new URL only if `urlparse(url).hostport == urlparse(self._base_url).hostport`. Otherwise it raises `SnapshotTransportError` with a clear cross-host message.
  * Document that the helper drops the auth header before re-issuing if the host check fails (defensive).
* **Testing.** Extend `tests/unit/test_http_client_redirect.py` with one safe-hop case and one cross-host rejection case.
* **Estimated effort.** 1.5h.

#### SEC-03c: Integration test on the destination

* **Context.** A high-fidelity scenario test backs the unit work.
* **Requirements.**
  * Add `tests/integration/test_import_redirect_safety.py` running `run_import` against a mocked destination that returns `302 Location: http://attacker.example/` on the first POST. Assert no second request is sent with the `Authorization: Token ...` header.
* **Testing.** Run the integration test.
* **Estimated effort.** 1.5h.

### SEC-04: Manifest persists full `source_url`

Severity medium. Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#SEC-04`.

#### SEC-04b: Assert no URL leaks in the export reproducibility test

* **Context.** Existing `tests/integration/test_export_reproducibility.py` asserts byte-for-byte parity; extend it to cover redaction.
* **Requirements.**
  * Add an assertion that no manifest field contains `http://` or `https://` substrings.
  * Add a parallel assertion that no jsonl file under `snapshot/` contains the source URL.
* **Testing.** Run the updated integration test.
* **Estimated effort.** 1h.

### SEC-05: Destination response bodies in audit log can leak tokens

Severity medium. Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#SEC-05`.

#### SEC-05a: Add `_redact_body` helper in `http/client.py`

* **Context.** The helper is the single chokepoint for the redaction logic.
* **Requirements.**
  * Implement `_redact_body(body: str) -> str`: strip lines matching `Authorization:`, replace `Token [0-9a-fA-F]+` with `Token <redacted>`, strip HTML `<script>` and `<style>` blocks (use a non-greedy regex).
  * Keep the helper at module scope so unit tests can import it directly.
* **Testing.** Add `tests/unit/http/test_redact_body.py` covering each redaction case in isolation.
* **Estimated effort.** 1.5h.

#### SEC-05b: Apply redaction in `NetboxHTTPError.__init__`

* **Context.** `http/client.py:118-127` formats the body on demand; redaction at construction time covers both audit and stderr paths.
* **Requirements.**
  * In `NetboxHTTPError.__init__`, call `_redact_body(body)` before storing on `self.body`.
  * Confirm both `__str__` and downstream consumers see the redacted form.
* **Testing.** Add `tests/unit/http/test_netbox_http_error_redaction.py` constructing the error with a body containing `Authorization: Token deadbeef` and asserting `error.body` is sanitised.
* **Estimated effort.** 1h.

#### SEC-05c: Audit-log redaction end-to-end test

* **Context.** Prove the redaction reaches `audit.jsonl`.
* **Requirements.**
  * Add `tests/integration/test_audit_redaction.py` driving a failing POST whose response body contains `Authorization: Token deadbeef`. Assert no token bytes land in `audit.jsonl`.
* **Testing.** Run the integration test.
* **Estimated effort.** 1h.

### SEC-06: `--audit-out` sidecar `preflight-bypass.jsonl` location

Severity low. Parent rationale lives in `docs/audits/20260616-architectural-and-security-audit.md#SEC-06`.

#### SEC-06a: Co-locate `preflight-bypass.jsonl` inside `<snapshot_dir>` plus add `--bypass-out`

* **Context.** `import_cli.py:307` derives the sidecar from `audit_path.with_name`.
* **Requirements.**
  * Default `bypass_path` to `<snapshot_dir>/preflight-bypass.jsonl`.
  * Add `--bypass-out PATH` flag to override explicitly.
  * Update `--help` text to call out the new default.
* **Testing.** Add `tests/unit/test_import_cli_bypass_out.py` asserting: default lands inside snapshot_dir; explicit `--bypass-out /tmp/x.jsonl` overrides; passing both `--audit-out` and `--bypass-out` does not collide.
* **Estimated effort.** 1h.

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

Per the audit on 2026-06-16, every ticket whose code has shipped
has been removed from the open backlog. Git history is the
authoritative implementation record. `git log --oneline TODO.md`
shows the audit commit and every prior body update; the matching
feat/fix/test/refactor/docs commits in `src/`, `tests/`, and
`docs/` carry the implementation detail per ticket.
