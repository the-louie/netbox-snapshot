# Changelog

The snapshot format follows the version recorded in `manifest.json`.
Per REL-02, this file captures what changed in each snapshot
format bump.

The project itself uses semantic versioning at the package level.

## Snapshot format 1, 2026-06-14

* Initial snapshot format. Layout per `docs/implementation/03-snapshot-storage.md`.
* Manifest schema:
  * `version: int`
  * `source_url: str`
  * `netbox_version: str`
  * `nbsnap_version: str`
  * `created_at: ISO 8601 timestamp`
  * `counts: dict[content_type, int]`
  * `perf: dict[label, seconds]`
  * `deferred_edges: list[{child, parent, field, nullable, is_m2m}]`
* JSONL row shape: `{"natural_key": [...], "body": {...}}`.
* `flags.jsonl` records install-local exclusions.
* `progress.jsonl` is an append-only resume log.

## Package versions

### 0.0.1, 2026-06-14

* Initial design + implementation pass.
* Phase 0 through Phase 9 implemented.
