# RES-08, v1.1 renderer-parity dataset

Status: **Decided**, 2026-06-14.

## Context

Renderer-parity tests (`TEST-08*`) need a stable, representative
dataset against which the `nb2kea` reference renderers can run on
both sides of a round-trip. v1.0 uses the in-tree seed fixtures
under `tests/fixtures/seed/`. v1.1 needs a larger and more varied
dataset.

## Decision

**Synthesise the v1.1 dataset from the production source NetBox**
via a one-off export run, scrubbed of install-local fields and any
dns_name values. The synthesised dataset lives in
`tests/fixtures/renderer_parity/v1.1.tar.zst` so it can be
checked into the repo, but the synthesis script (`scripts/synthesise_renderer_parity.py`)
ships separately so an operator can regenerate it against their
own production NetBox.

The dataset is **frozen** once committed; per-release bumps go
through a dedicated PR that re-runs all renderer-parity tests
against the new dataset.

## What would force a flip

* The production source NetBox is decommissioned before v1.1.
  In that case, we synthesise from the largest accessible test
  NetBox instead.
* A renderer needs a field shape that the production source does
  not contain, in which case we extend the seed fixtures rather
  than synthesising.

## Cross-references

* `PLAN.md` Phase 8.
* `TEST-08a1-c3` in `TODO.md`.
