# Project documentation index

Design and reference docs for the NetBox portable-snapshot project. Read in
order on first pass; later docs build on the model defined in the earlier
ones.

| # | Doc | What it covers |
| :--- | :--- | :--- |
| 00 | [Problem statement](00-problem-statement.md) | Why this project exists, the four NetBox obstacles, why the public-tooling landscape doesn't cover it. |
| 01 | [Scope](01-scope.md) | What "the snapshot" carries vs. omits, restated more tightly than `goals.md`. |
| 02 | [Data-model scope](02-data-model-scope.md) | Per-NetBox-app inventory of object types we export, with the renderer-minimum subset marked. |
| 03 | [Dependency graph](03-dependency-graph.md) | The model graph and ordering strategy (topological sort, two-phase cycle break). |
| 04 | [Snapshot format](04-snapshot-format.md) | On-disk layout, file naming, natural-key conventions, manifest schema. |
| 05 | [Export/import workflow](05-export-import-workflow.md) | The runtime pipeline on each side, idempotency rules, recovery from partial runs. |
| 10 | [Known gaps & open questions](10-known-gaps.md) | Plugins, custom auth, install-local edge cases, and what we plan to do about them. |

## Friction deep-dives

The ten highest-friction areas have dedicated docs under
[`frictions/`](frictions/00-overview.md). Each holds an extreme-detail
problem description and 5-10 mitigations with references. Read the
overview first; individual dives are linked from the design docs that
depend on them.

| # | Area | Doc |
| :--- | :--- | :--- |
| 01 | Cyclical foreign keys | [frictions/01](frictions/01-cyclical-foreign-keys.md) |
| 02 | Content types & generic FKs | [frictions/02](frictions/02-content-types-and-generic-fks.md) |
| 03 | Custom field & choice set evolution | [frictions/03](frictions/03-custom-field-evolution.md) |
| 04 | Natural-key strategy | [frictions/04](frictions/04-natural-key-strategy.md) |
| 05 | Cable / cable-termination model | [frictions/05](frictions/05-cable-termination-model.md) |
| 06 | NetBox version drift | [frictions/06](frictions/06-netbox-version-drift.md) |
| 07 | Auth & secret portability | [frictions/07](frictions/07-auth-and-secret-portability.md) |
| 08 | Install-local references | [frictions/08](frictions/08-install-local-references.md) |
| 09 | Plugin objects & unknown schemas | [frictions/09](frictions/09-plugin-objects.md) |
| 10 | API scaling & rate limits | [frictions/10](frictions/10-api-scaling-and-rate-limits.md) |

## Reference (not authored here, but load-bearing)

* `/workspace/CLAUDE.md` — project-level context for the agent and future
  contributors.
* `/workspace/goals.md` — primary goals, success criteria, anti-goals.
* `/workspace/__reference/nb2kea/` — prior renderer project; specifically:
  * `CLAUDE.md` — network architecture context.
  * `reference_documentation/architecture_notes/11-netbox-data-requirements.md`
    — the renderer-side authoritative data contract this project must satisfy.
  * `reference_documentation/architecture_notes/07-naming-and-netbox-mapping.md`
    — Site/Location/Rack/Role field repurposing.
  * `reference_documentation/netbox/integrations/rest-api.md` —
    mirrored NetBox REST API guide.
  * `scripts/netbox_utils/netbox_common.py` — working NetBox HTTP client
    with retry/backoff and pagination, useful as an implementation
    reference.

## Operator documentation

The runbook and performance guide are split out so an
operator on call has a tight, command-shaped reference
without needing to read the design docs first:

* [`operator-runbook.md`](operator-runbook.md) — the three
  supported workflows (cold migration, parallel deployment,
  partial re-sync) plus the documented exit-code matrix.
* [`operator-performance.md`](operator-performance.md) —
  NetBox-side, front-proxy, GraphQL, and bulk-endpoint
  tuning decisions in evaluation order.

## Implementation notes

Per-decision rationales, measurements, and rejected
alternatives live under
[`implementation/`](implementation/00-INDEX.md). The
operator docs above cross-link back to specific notes
where the decision matters at runtime.

## Documentation conventions

* Markdown only. No diagrams as images — ASCII art if needed.
* Cross-link with relative paths.
* Each design doc names its **decisions**, **alternatives considered**, and
  **what would force a revisit**.
* When a design doc lands a decision, the affected operator-facing constraint
  also lands in `/workspace/goals.md` so the goals file stays the single
  scope authority.
