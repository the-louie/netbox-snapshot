# RES-03, snapshot file format on disk

Status: **Decided**, 2026-06-14.

Implements the `RES-03` ticket in `TODO.md`. The decision feeds
Phase 4 (export writer, `FEAT-14*`) and Phase 7 (operator workflow
docs).

## Context

`docs/04-snapshot-format.md` picked JSONL, one row per object,
grouped by content type. What is still open:

1. Raw JSONL on disk vs gzip-per-file vs a single archive.
2. How an operator hands a snapshot to a colleague.
3. The integrity hash that ties content together.

## Operator workflows the format must serve

We thought through three concrete scenarios:

* **Diff experience.** "Why does my destination NetBox have this
  extra interface?" — operator wants `git diff` between two
  snapshots, or at least `diff -u` on a checked-out tree.
* **Share as one artefact.** "Send me last night's snapshot." —
  operator wants a single file they can scp, attach, or post.
* **Cold storage.** "Keep monthly snapshots for a year." — operator
  wants compression.

## Decision

**Raw JSONL by default. Optional tarball with zstd compression.**

* On-disk default: a tree of `.jsonl` files, one per content type,
  inside a snapshot directory. This is the form the exporter
  writes and the importer reads.
* Optional packed form: `nbsnap pack <dir>` produces
  `<dir>.nbsnap.tar.zst`. `nbsnap unpack <file>` reverses it. Both
  CLI sub-commands are already stubbed in INFRA-02b and land in
  `FEAT-34` / `FEAT-35`.

This three-way split satisfies all three operator workflows:

* The diff experience is uncompromised, JSONL diffs cleanly.
* Sharing is one command away (`nbsnap pack`).
* Cold storage uses the packed form.

## Layout under the snapshot directory

```
snapshot/
├── manifest.json                    schema 1, see docs/04
├── schema/
│   └── openapi.json                 canonicalised schema
├── content_types.json               source content-type cache
├── status.json                      source NetBox version + plugins
├── dcim/
│   ├── sites.jsonl
│   ├── devices.jsonl
│   └── …
├── ipam/
│   ├── vlans.jsonl
│   └── …
├── extras/
│   ├── tags.jsonl
│   ├── custom-fields.jsonl
│   └── …
├── _deferred.jsonl                  Phase-2 import (cycle-closing patches)
├── progress.jsonl                   per-row checkpoints, append-only
└── flags.jsonl                      install-local exclusions, audit trail
```

`.jsonl` lines are sorted by natural-key tuple so a re-export
against the same source produces byte-identical files (modulo the
explicitly excluded fields).

## Naming convention for packed snapshots

```
<basename>.nbsnap.tar.zst
```

* `.nbsnap.tar.zst` is reserved, the importer refuses anything
  else.
* The double extension makes it obvious the artefact is a zstd
  tarball without forcing operators to remember a custom marker.

## Integrity hash

The packed form stores its content hash inside `manifest.json` and
emits a sidecar `<basename>.nbsnap.tar.zst.sha256` next to the
artefact.

Hash is computed over the canonicalised contents (the JSONL files
sorted by natural-key, with `manifest.json` stripped of the hash
itself, so the hash can be re-computed after unpack). Algorithm:
SHA-256.

The sidecar is documented as the authoritative integrity check.
The hash inside `manifest.json` is informational, helpful for a
quick "are these two snapshots the same" eyeballing in a JSON
viewer.

## Cold-storage notes

* zstd level 19 by default for `nbsnap pack`, the level that
  trades ~3% extra compression time for ~15% smaller payload on
  representative NetBox dumps. Operators can pass `--level N`.
* `zstd` is present on every recent Linux distribution and on the
  CI runner base image (`ubuntu-latest` ships it). The INFRA-04
  CI workflow gains a one-line `zstd --version` smoke step in
  the integration job so an absence is caught early instead of at
  FEAT-34 implementation time.
* The Python binding `zstandard` is the natural runtime pick. We
  defer adding it to `[project].dependencies` until `FEAT-34`
  lands so the v1 install stays light when an operator does not
  need pack/unpack.

## Cross-references

* `docs/04-snapshot-format.md`, the original JSONL decision.
* `PLAN.md` Phase 4 (writer), Phase 7 (operator workflows).
* `FEAT-34`, `FEAT-35` in `TODO.md`, the pack/unpack tickets.
