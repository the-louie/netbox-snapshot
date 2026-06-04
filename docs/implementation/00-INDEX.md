# Implementation notes index

One-line summary per decision document. The notes themselves
carry the rationale, the measurements, and the rejected
alternatives. The runbooks (`docs/operator-runbook.md`) and the
performance guide (`docs/operator-performance.md`) cross-link
back here for the deeper context.

- [01 — HTTP client](01-http-client.md) — why a thin `requests`
  wrapper with explicit retry/backoff and a hard
  source-readonly guard rail.
- [02 — Runtime](02-runtime.md) — Python 3.11 floor and the
  asyncio-vs-sync decision.
- [03 — Snapshot storage](03-snapshot-storage.md) — JSONL
  layout, manifest schema, natural-key encoding.
- [04 — Graph library](04-graph-library.md) — Tarjan SCC plus
  `graphlib.TopologicalSorter` for the dependency planner.
- [06 — GraphQL adoption](06-graphql-adoption.md) — RES-06 trigger
  conditions and the >30% wall-time gain threshold.
- [07 — Bulk endpoint adoption](07-bulk-endpoint-adoption.md) —
  RES-07 trigger conditions, the per-record error trade-off.
- [08 — Renderer parity dataset](08-renderer-parity-dataset.md) —
  TEST-08a1/a2/a3 fixture shape, the synthetic hand-built
  network used for the headline acceptance gate.

When adding a new implementation note:

1. Number it sequentially with a two-digit prefix.
2. Add a one-line entry to this index in the same commit.
3. Cross-link from any operator-facing document that names
   the decision.
