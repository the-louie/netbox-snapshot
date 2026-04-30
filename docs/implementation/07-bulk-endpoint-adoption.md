# RES-07, bulk endpoint adoption schedule

Status: **Decided**, 2026-06-14.

## Context

`docs/frictions/10` M6 names Cables, Interfaces during initial
population, and IPAddresses against an empty destination as the
candidates for bulk POST/PATCH. Bulk endpoints are append a list
of objects in a single call.

## Decision

**Adopt bulk endpoints for Cables and IPAddresses in Phase 8,
gated by the v1 import being clean.** Interfaces stay non-bulk
until Phase 9 because the renderer-parity tests are the
load-bearing assertion and bulk Interface POST has tripped a
NetBox bug as recently as v4.5.

Selection rule for adoption:

* Bulk operates per content type. The driver feeds a batch from
  the snapshot's JSONL file, sizes batches to 200 rows by
  default (configurable via `--bulk-size`).
* On a bulk failure, fall back to per-row upsert so a single bad
  row does not abort the whole batch.

## What would force a flip

* Bulk endpoints reduce import time by less than 30% on the
  reference dataset, in which case the added complexity is not
  worth it.
* A NetBox release breaks bulk POST validation in a way we cannot
  pin around.

## Cross-references

* `PLAN.md` Phase 8.
* `docs/frictions/10-api-scaling-and-rate-limits.md` M6.
