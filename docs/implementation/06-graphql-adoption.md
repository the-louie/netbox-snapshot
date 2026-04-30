# RES-06, GraphQL adoption schedule

Status: **Deferred to Phase 8 measurement**, 2026-06-14.

## Context

NetBox exposes a GraphQL endpoint that can replace many REST list
calls. The promise is two-fold: smaller payloads (request only the
fields you want) and fewer round trips (one query for a nested
shape vs N paginated calls).

## Decision

**Defer until Phase 8 has a measured baseline.** The export-side
read pass is single-worker and bounded by the renderer-minimum
data set; until we have a wall-clock number that crosses
PLAN.md's 10-minute target, GraphQL is a guess.

When the measurement lands, GraphQL adoption is justified iff:

* The single-query wall-clock is at least 30% under the REST
  equivalent on the same data set.
* The GraphQL schema covers every field the snapshot writer needs
  (custom fields and plugin-extended schemas often lag the REST
  surface).
* The destination NetBox version's GraphQL implementation is
  stable enough that the integration suite tests pass.

## What would force a flip earlier

A production source NetBox where the REST pagination is so slow
that the 10-minute target cannot be met. In that case, GraphQL
becomes a Phase 5 dependency rather than a Phase 8 polish item.

## Cross-references

* `PLAN.md` Phase 8.
* `docs/frictions/10-api-scaling-and-rate-limits.md` M6 / M7.
