# RES-04, NetworkX vs hand-rolled Tarjan

Status: **Decided**, 2026-06-14.

Implements the `RES-04` ticket in `TODO.md`. Phase 2 of `PLAN.md`
needs a dependency graph builder and a strongly-connected-component
(SCC) detector. The decision is whether to depend on `networkx` or
to land a small Tarjan implementation in-tree.

## Candidates

| Option | Dependency cost | Functional cost |
| :--- | :--- | :--- |
| `networkx` | one extra runtime dep (~3 MB on disk, transitive `numpy` not required for our subset) | minimal, every graph operation is already provided |
| Hand-rolled | zero | one ~120-line module with Tarjan + topo sort |

## Trade-off summary

* **Scope alignment.** We need: add node, add edge, SCC detection,
  topological sort. NetworkX exposes hundreds of algorithms; we
  use four. A hand-rolled module aligns the dependency surface
  with the actual need.
* **Determinism.** The hand-rolled Tarjan is easier to make
  deterministic (sorted iteration order of children) than the
  NetworkX equivalent, which depends on hash-iteration of dicts.
  Determinism matters because the plan output is exposed to
  operators via `nbsnap plan` and they will compare runs.
* **Testability.** A hand-rolled module is two unit tests away
  from full coverage. NetworkX's behaviour is its own surface
  with its own version drift.
* **Educational value.** The intended audience (low to mid-tier
  developers) benefits from reading a self-contained Tarjan
  implementation with comments that explain the recursion stack
  and the low-link.

## Decision

**Hand-rolled Tarjan in `src/nbsnap/graph/algo.py`.** No NetworkX
runtime dependency.

## What would force a flip

* If we ever need more than three NetworkX algorithms outside the
  Tarjan + topo sort + connected-components core, the dependency
  cost would be justified.
* If a NetBox model emerges that produces hundreds of thousands of
  edges in a single SCC, a NetworkX-backed implementation might
  outperform a pure-Python Tarjan with explicit stacks.
* If Python's iterative recursion limits become an issue for very
  deep graphs, we may swap to NetworkX's iterative SCC routine.

## Cross-references

* `PLAN.md` Phase 2.
* `docs/03-dependency-graph.md`, the original analysis that motivates
  the graph at all.
* `docs/frictions/01-cyclical-foreign-keys.md`, the load-bearing
  cycle (Device.primary_ip4 -> IPAddress -> Interface -> Device).
