# Master plan

This plan converts the design docs in `docs/` into a phased delivery
schedule. The phases are ordered so each one **unblocks** the next:
finishing a phase produces a working artefact the following phase can
build on.

The two principles that drive the order:

1. **Schema-first, write-last.** Every phase that produces *snapshot
   bytes* depends on the schema discovery phase that comes before it.
   Every phase that produces *destination writes* depends on read-side
   support being proven first.
2. **Renderer parity is the acceptance test.** We can declare a phase
   done when the renderer-parity check in `goals.md` success criterion 1
   advances. The earliest phase where this becomes meaningful is
   Phase 5 (Import engine), so phases 0–4 are graded by self-tests
   and unit tests; phases 5+ also have to satisfy the renderer-parity
   contract.

## Phase summary

| # | Phase | Output | Gate to next phase |
| :--- | :--- | :--- | :--- |
| 0 | Foundation | Repo skeleton, language pick, CI baseline, test NetBox stack | `nbsnap --help` runs against an empty repo; CI green |
| 1 | Schema discovery | HTTP client, OpenAPI fetch, content-type cache, plugin enum, version probe | Round-trip schema fetch against two netbox-docker instances |
| 2 | Graph construction | Node/edge model, Tarjan SCC, deferred-edge plan, topo sort | `nbsnap plan` prints the full ordered write plan for a real NetBox |
| 3 | Natural key system | Per-model NK spec table, resolver lib, validation | NK uniqueness verified on a real NetBox; collision report empty |
| 4 | Export engine | Per-endpoint extractor, JSONL emitter, manifest, flag file | `nbsnap export` produces a byte-stable snapshot; re-export is identical |
| 5 | Import engine | Upsert engine, FK resolver, phase-1 + phase-2 writers | `nbsnap import` lands a fresh snapshot on an empty NetBox |
| 6 | Verification | Round-trip self-test, diff command, renderer-parity test against nb2kea | Renderer-parity test green for the reference dataset |
| 7 | Operational polish | Dry-run, resumability, structured logging, runbook | Operator can drive an unattended import and read a clean run log |
| 8 | Extensions | Plugin extension API, reference plugin extension, GraphQL evaluator | At least one plugin's data round-trips through an extension |
| 9 | Hardening | Security review, scale test, release process | A signed, versioned release artefact published |

## Phase 0 — Foundation

**Goal.** Produce the smallest possible repo skeleton that supports
every later phase without re-architecting.

**Scope.**

* Language: Python 3.11+ (matches the `nb2kea` codebase; NetBox itself
  is Python; `pynetbox` exists as a fallback library).
* Build system: `pyproject.toml` with `hatchling` or `setuptools`;
  pin minimal direct dependencies.
* Runtime dependencies (planned, justified individually in
  `RES-` tasks): `httpx` (better timeouts than `requests`),
  `pydantic` v2 (typed config), optional `pyyaml` for `--replacement-map`,
  optional `jsonschema` for snapshot format validation. Avoid heavy
  deps; the `nb2kea` precedent of `curl` + stdlib is the floor.
* Dev dependencies: `pytest`, `pytest-asyncio` (if `httpx` async),
  `ruff`, `mypy`, `pre-commit`.
* CLI: a single entry-point `nbsnap` with sub-commands
  (`export`, `import`, `plan`, `diff`, `verify`).
* Repository layout (target):

  ```
  /workspace
  ├── pyproject.toml
  ├── src/nbsnap/
  │   ├── cli.py
  │   ├── config.py
  │   ├── http/
  │   ├── schema/
  │   ├── graph/
  │   ├── natkey/
  │   ├── export/
  │   ├── import_/
  │   ├── verify/
  │   └── plugins/
  ├── tests/
  │   ├── unit/
  │   ├── integration/
  │   └── fixtures/
  ├── examples/
  └── docs/   (existing)
  ```

* Test stack: two `netbox-community/netbox-docker` instances stood up
  via `docker compose` for integration tests; the project ships a
  helper script and fixtures.
* CI: GitHub Actions with three jobs — `lint`, `unit`, `integration`.
  Integration runs against the docker-compose stack; uses cached
  layers.

**Exit criteria.**

* `nbsnap --help` runs.
* `pytest -q` passes (zero tests OK; the harness must be wired).
* CI runs all three jobs green on a no-op PR.
* `docker compose up` produces two NetBox instances reachable on
  different ports.

## Phase 1 — Schema discovery

**Goal.** Read everything we need to **plan** an export or import,
without producing any snapshot bytes yet.

**Scope.**

* HTTP client (`nbsnap.http.client`) library choice locked in
  `docs/implementation/01-http-client.md` (RES-01), `requests>=2.31,<3`.
  Runtime model is sync v1 per `docs/implementation/02-runtime.md`
  (RES-02), with a documented async-swap trigger.
* HTTP client (`nbsnap.http.client`) with:
  * Token-from-env auth (mirroring `nb2kea`).
  * Configurable timeout (default 30s; per-endpoint overrides).
  * Retry/backoff on curl-equivalent exits, HTTP 429, HTTP 5xx.
  * Respects `Retry-After`.
  * TLS verification on by default; opt-out per-instance.
  * Pagination iterator that follows `next`.
* OpenAPI fetch: `GET /api/schema/?format=json`, cache to disk for the
  run.
* `GET /api/status/` for version + plugin list.
* `GET /api/extras/content-types/` for the content-type cache.
* `GET /api/plugins/` for enabled plugins.

**Outputs.**

* `nbsnap.schema.OpenAPI` — parsed schema with helpers like
  `iter_endpoints()`, `field_spec(content_type, field)`.
* `nbsnap.schema.ContentTypeCache` — bidirectional `(app, model) ↔ id`.

**Exit criteria.**

* Against the two-instance test stack, both can be fetched in <30s.
* The cache survives one instance restart (no stale references).
* Unit tests cover: paginated iteration, retry on 429, retry on
  transient 5xx, abort on 4xx, abort on schema mismatch.

## Phase 2 — Graph construction & planning

**Goal.** Produce a deterministic write-order plan from the schema.

**Scope.**

* `nbsnap.graph.Graph`: directed graph over content types.
* Edge inference from OpenAPI: each FK field is an edge from `child →
  parent`, tagged `(field, nullable, required, m2m)`.
* In-scope filter: drop nodes per `docs/02-data-model-scope.md`
  exclusion list.
* Cycle detection: Tarjan's SCC on the graph; for each SCC pick one
  deferred edge per the algorithm in `docs/03-dependency-graph.md`.
* Topological sort over the acyclic remainder.
* `nbsnap plan` subcommand: prints the ordered write plan and the
  deferred-edge list for a given source.

**Exit criteria.**

* `nbsnap plan` on a fresh netbox-docker produces a stable order across
  three invocations.
* The deferred-edge list always contains `Device.primary_ip4`,
  `Device.primary_ip6`, and `VirtualChassis.master`.
* The plan output round-trips through JSON without losing information.
* Unit tests cover: cycle detection on a synthetic graph with multiple
  SCCs, deferred-edge selection priority (nullable > non-nullable),
  topo sort stability.

## Phase 3 — Natural key system

**Goal.** Identify every object by a tuple the destination NetBox can
also resolve.

**Scope.**

* `nbsnap.natkey.spec`: the per-model spec table (mirrors
  `docs/02-data-model-scope.md`).
* `nbsnap.natkey.resolve(record, content_type) -> tuple`.
* Polymorphic FK natural-key encoding for IPAddress.assigned_object,
  Cable terminations, etc.
* Duplicate-key audit pass: walks every type, asserts no two records
  share a NK.
* `nbsnap verify-natkeys` subcommand for ad-hoc audits.

**Exit criteria.**

* Against the reference NetBox dataset, the duplicate-key audit reports
  no duplicates.
* Unit tests cover every NK strategy from `02-data-model-scope.md`.
* Plugin-author docs show how to register a custom NK strategy (stub OK
  in Phase 3; full extension API in Phase 8).

## Phase 4 — Export engine

**Goal.** Produce a byte-stable snapshot on disk.

**Scope.**

* Per-endpoint extractor: paginated fetch, field allowlist filter
  (from OpenAPI write schema), FK → NK rewrite, install-local
  classifier.
* JSONL serializer: stable sort by NK, JSON-key insertion order.
* Manifest writer (schema per `docs/04-snapshot-format.md`).
* Deferred-FK file emitter.
* Install-local flag file emitter.
* Progress checkpointing (resume mid-run).
* `nbsnap export` CLI: `--url`, `--token`, `--out`, `--scrub`,
  `--replacement-map`, `--page-size`, `--max-concurrent`.

**Exit criteria.**

* Two consecutive exports of the same NetBox state produce byte-identical
  snapshot trees (modulo `manifest.exported_at`).
* The renderer-minimum endpoint list from `docs/02-data-model-scope.md`
  is read at least once during export (contract test).
* The install-local flag file lists at least one expected finding from
  the reference dataset.
* Integration test against netbox-docker passes.

## Phase 5 — Import engine

**Goal.** Apply a snapshot to a fresh NetBox.

**Scope.**

* Pre-flight: version skew check, plugin parity, OpenAPI hash compare.
* Natural-key index builder (`GET ?brief=true` per type).
* Upsert engine: GET-by-NK, then POST or PATCH (skip-if-equal).
* FK resolver: polymorphic, hits the NK index.
* Phase-1 writer: walks each `.jsonl` file in plan order.
* Phase-2 writer: walks `_deferred.jsonl`.
* Error categorisation per `docs/05-export-import-workflow.md`.
* `nbsnap import` CLI: `--dry-run`, `--max-version-skew`,
  `--reject-existing`, `--allow-source-install-ips`,
  `--include-password-hashes`.

**Exit criteria.**

* Importing a freshly-exported snapshot into an empty NetBox produces
  the expected object counts.
* Second invocation of `import` against the same destination produces
  zero writes (idempotency).
* All five error categories have at least one unit test.
* Integration test against two netbox-docker instances passes.

## Phase 6 — Verification

**Goal.** Prove renderer parity and produce a diff tool for ongoing
audits.

**Scope.**

* `nbsnap diff snapshot-a/ snapshot-b/`: produces a human-readable
  delta limited to non-excluded fields.
* Round-trip test harness: export A → import B → export B → diff.
* Renderer-parity test: clone `__reference/nb2kea/`, run all three
  renderers against A and B, diff outputs.
* `nbsnap verify`: post-import verification that the destination's
  re-exported snapshot matches the source's snapshot.

**Exit criteria.**

* Round-trip test passes on the reference dataset.
* Renderer-parity test passes (byte-identical Cisco / Junos / Kea
  output between source and destination).
* CI runs the round-trip + renderer-parity test on every PR.

## Phase 7 — Operational polish

**Goal.** Make the tool usable by an operator who has never read the
design docs.

**Scope.**

* Structured JSON logging (one event per write attempt).
* Per-phase progress bars (only on TTY).
* Run summary table at end of each invocation.
* Resumable runs (already designed in Phase 4 progress checkpointing;
  here we polish the operator UX).
* `docs/operator-runbook.md`: step-by-step for the common workflows
  (cold migration, parallel deployment, partial re-sync).
* `docs/operator-performance.md`: how to tune NetBox/WAF for the tool.
* Error messages: every failure mode has a "what to do next" line.

**Exit criteria.**

* An operator with the runbook can complete a cold migration end-to-end
  without reading source.
* Run logs are tail-able JSONL; piping through `jq` produces useful
  views.
* No `print(...)` calls in `src/`; everything goes through the logger.

## Phase 8 — Extensions

**Goal.** Open the door to plugin-aware exports without re-architecting
the core tool.

**Scope.**

* Extension discovery via Python entry-points
  (`nbsnap.plugin` group). See `docs/implementation/02-runtime.md`
  (RES-02) for the async-swap trigger that gates parallel reads.
* Per-plugin registration shape (see
  `docs/frictions/09-plugin-objects.md`).
* GraphQL evaluator: benchmark vs REST for the export's read passes.
  Adopt if measurably faster; otherwise document and skip.
* Bulk endpoint adoption: cables and high-volume types only, per
  `docs/frictions/10-api-scaling-and-rate-limits.md` M6.
* Reference extension: `nbsnap-netbox-bgp` in a separate repo.

**Exit criteria.**

* The reference extension round-trips `netbox-bgp` data on a test
  instance.
* GraphQL benchmark + decision is documented.
* Bulk endpoints reduce import time on the reference dataset by ≥30%
  or are explicitly rejected with measurement-backed reasoning.

## Phase 9 — Hardening & release

**Goal.** Ship a v1.0 the operator can rely on.

**Scope.**

* Security review: token handling, TLS posture, install-local
  classification correctness.
* Scale test against a synthetic 50,000-object NetBox.
* Release process: versioning (semver), changelog (Keep a Changelog),
  signed artefacts (PyPI + GitHub release).
* Long-form CHANGELOG.md for the snapshot format itself.

**Exit criteria.**

* Security review checklist green (and committed to repo).
* Scale test completes within 2× the design budget in
  `docs/05-export-import-workflow.md`.
* `pip install nbsnap` installs from PyPI.
* GitHub release with signed artefacts is published.

## Cross-phase considerations

### Test environment

The `netbox-community/netbox-docker` stack is the integration test
backbone across phases 1–8. Two instances are stood up on different
ports (e.g. 8000 for "source", 8001 for "destination"). Fixtures
populate the source from JSON files committed to `tests/fixtures/`.
Tests run the tool end-to-end and assert outcomes.

### Performance budget

Per `docs/05-export-import-workflow.md`:

* Export: <2 minutes for ~10k-object NetBox.
* Import (clean destination): <5 minutes for ~10k-object NetBox.
* Import (idempotent re-run): <2 minutes for ~10k-object NetBox.

Phases 4 and 5 must hit these on the reference dataset. Phase 9 scales
the test to 50k objects.

### Documentation cadence

Every phase ends with a doc update pass:

* New design decisions land in the matching `docs/` file.
* The phase's own implementation notes go into `docs/implementation/`
  (to be created in Phase 0).
* Friction-doc references are added or updated as we learn more.

### Risk register

* **R1 — NetBox 4.7 ships before Phase 6.** Mitigation: Phase 1's
  OpenAPI hash check surfaces drift; we pin the test stack to 4.6.x and
  add a 4.7 compatibility task to the TODO when 4.7 lands.
* **R2 — `netbox-docker` upstream changes break CI.** Mitigation: pin
  the netbox-docker tag in CI; periodic bump as a separate `INFRA-` task.
* **R3 — The two-instance docker-compose stack does not detect
  ContentType id drift.** Mitigation: Phase 1's test seeds different
  plugin sets in each instance to force the IDs to diverge.
* **R4 — Plugin authors don't write extensions.** Mitigation: ship the
  reference extension early in Phase 8 and document the contract; v1.0
  works for stock NetBox without any extension.
* **R5 — Phase 5's idempotency contract is harder than it looks.**
  Mitigation: every phase 5 task is followed by an idempotency test
  task in the TODO; we don't sign off a write path without proving
  re-runs are no-ops.

## Open decisions (resolve in TODO `RES-` tasks)

The tasks under `RES-` in `TODO.md` are decisions that gate phases and
must be resolved early:

* HTTP library (`httpx` vs `requests` vs stdlib `urllib`).
* Async vs sync runtime.
* Snapshot format compression (raw JSONL vs `.jsonl.gz` per file vs
  single `.tar.zst`).
* Bulk endpoint adoption schedule.
* GraphQL adoption schedule.

The TODO calls these out individually; the master plan lists them here
only to flag they exist.
