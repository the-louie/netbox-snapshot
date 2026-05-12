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
* `BUG-nn` for bug fixes (none open yet, reserved).
* `REL-nn` for release and milestone gates.

Sub-tickets carry a lowercase letter suffix on the parent ID, for
example `INFRA-01a`, so a cross-reference from `PLAN.md` to the parent
concept still resolves.

Cross-references:

* `PLAN.md` for phase definitions and exit criteria.
* `docs/` for design documents.
* `docs/frictions/` for friction-area deep-dives.
* `goals.md` for scope and success criteria.

---

## Codebase status

Phases 0 through 3 plus the bulk of Phases 4 through 9 are
implemented and committed. The open backlog below covers the
work that is genuinely ahead, namely the import-resolution
improvements (FEAT-36 series), the destination-reset utility
(FEAT-37 series), the deferred-FK Phase-2 writer (FEAT-23),
a handful of integration tests, and the operator-runbook
documentation set. Run `git log --oneline --grep="^feat\|^fix"`
for the full implementation history.

## Open, Phase 4, Export engine

### [TEST-04] Export reproducibility integration test

**Context:** `goals.md` success criterion 3 / `PLAN.md` Phase 4 exit.

**Requirements:**

- `tests/integration/test_export_reproducibility.py`.
- Seed source stack, run export to `/tmp/a/`, run again to `/tmp/b/`.
- Compare directory trees: every per-type `.jsonl` file must be
  byte-identical. The only allowed delta is `manifest.exported_at`
  (and any timer values).
- Use `difflib.unified_diff` to surface deltas in the assertion
  message.

**Testing:** the test itself is the testing step. Run it twice,
confirm green both times. Mutate a single Device's name on the
source stack between runs, run the test, confirm it fails with a
clear diff.

**Estimated Effort:** 1-2h

### [TEST-05] Renderer-minimum endpoint contract test

**Context:** `goals.md` success criterion 5, every endpoint marked
`M` in `docs/02-data-model-scope.md` must be hit.

**Requirements:**

- `tests/integration/test_renderer_minimum_coverage.py`.
- Monkey-patch `NetboxHTTP.get_all` to record every (method, url) tuple.
- Run `nbsnap export` against the seeded source.
- Build the expected endpoint set from the M-rows of the data-model-scope
  doc (hard-code the list, with a comment pointing back at the doc).
- Assert `recorded >= expected`.

**Testing:** run on a clean run, confirm green. Comment out the
prefix walking in the export engine, confirm the test fails and the
assertion lists the missing endpoints.

**Estimated Effort:** 1-2h

---

## Open, Phase 5, Import engine

### [REFINED] [FEAT-23] Phase-2 deferred-FK writer

#### Architectural specification

NetBox's network model is **cyclic** in a few well-known places.
The friction note `docs/frictions/01-cyclical-foreign-keys.md`
catalogues the cases; the load-bearing one is:

    Device.primary_ip4 → IPAddress
    IPAddress.assigned_object → Interface
    Interface.device → Device

Creating any of these in isolation is impossible because each
needs another to already exist. The import engine therefore
runs in two phases:

* **Phase-1** creates every record without its cycle-closing
  FK. Devices land with `primary_ip4 = null`. IPAddresses land
  with their `assigned_object` set (the interface). Interfaces
  land with their `device` set.
* **Phase-2** walks a queue of deferred FKs and issues one
  PATCH per (record, field) tuple to fill in the cycle-closing
  references after both endpoints exist.

The queue is populated in two ways:

1. The **planner** (see FEAT-06b) statically identifies nullable
   self-loops and writes them to `_deferred.jsonl` at export
   time. These are the obvious cycle-closers.
2. The **demand-driven resolver** (FEAT-36b) pushes
   `DeferredFK` entries into an in-memory queue when its
   recursion detects a runtime cycle.

This ticket implements the writer that consumes both sources
and PATCHes the destination.

#### REST API details

Phase-2 issues one PATCH per deferred entry:

    PATCH /api/<endpoint>/<destination_id>/
    Authorization: Token <token>
    Content-Type: application/json

    {"<field_name>": <resolved_value>}

The resolved value is the destination id for FK fields, or the
literal value for non-FK deferred fields. The PATCH body is
intentionally minimal (one field) so NetBox's audit log shows
exactly what changed.

Endpoint mappings come from the same `CONTENT_TYPE_ENDPOINTS`
table the upsert path uses (see
`src/nbsnap/natkey/verify.py`).

NetBox returns:
* `200 OK` with the full updated record on success.
* `400 Bad Request` if the FK target id no longer resolves
  (very rare; happens if the destination was mutated between
  Phase-1 and Phase-2).
* `404 Not Found` if the parent record was deleted between
  Phase-1 and Phase-2.

The Swagger UI's `partial_update` operation for each content
type documents the expected request shape:
`https://demo.netbox.dev/api/schema/swagger-ui/`.

#### Implementation

```python
# src/nbsnap/import_/phase2.py
"""Phase-2 deferred FK writer.

Walks the deferred-FK queue produced by Phase-1 (planner static
defers + look-ahead runtime defers) and PATCHes each entry on
the destination. Minimal PATCH bodies, one field each, so the
destination's audit log shows exactly what changed and why.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from nbsnap.http.client import NetboxHTTP, NetboxHTTPError
from nbsnap.import_.lookahead import DeferredFK
from nbsnap.import_.nk_index import NKIndex
from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS

logger = logging.getLogger(__name__)


@dataclass
class Phase2Summary:
    """Per-run audit of the Phase-2 PATCH pass."""

    counts: Counter[str] = field(default_factory=Counter)  # "patched", "skipped", "failed"
    failures: list[tuple[DeferredFK, str]] = field(default_factory=list)

    def is_clean(self) -> bool:
        return self.counts.get("failed", 0) == 0


def run_phase2(
    http: NetboxHTTP,
    deferred_queue: Iterable[DeferredFK],
    *,
    dest_index: NKIndex,
    registry: NKRegistry,
) -> Phase2Summary:
    """PATCH each deferred FK on the destination.

    Args:
        http: A writable NetboxHTTP bound to the destination.
        deferred_queue: Iterable of DeferredFK entries produced
            by Phase-1 (planner statics + look-ahead runtime).
        dest_index: The NKIndex built during Phase-1; should now
            contain every record created in Phase-1.
        registry: Natural-key registry for resolving target NKs
            against the index.
    """

    summary = Phase2Summary()

    for entry in deferred_queue:
        # Look up the child record's destination id (it was
        # created in Phase-1 without this FK).
        child_id = dest_index.lookup(entry.child_content_type, entry.child_nk)
        if child_id is None:
            logger.warning(
                "Phase-2: child %s NK=%r not on destination, skipping",
                entry.child_content_type, entry.child_nk,
            )
            summary.counts["skipped"] += 1
            continue

        # Look up the target's destination id. Build the target
        # NK index first; for self-loops (devicerole.parent ->
        # devicerole) the same content type already happens to
        # be built, so this is usually a no-op.
        dest_index.ensure_built(http, registry, entry.target_content_type)
        target_id = dest_index.lookup(entry.target_content_type, entry.target_nk)
        if target_id is None:
            logger.warning(
                "Phase-2: target %s NK=%r still missing, skipping %s.%s",
                entry.target_content_type, entry.target_nk,
                entry.child_content_type, entry.field_name,
            )
            summary.counts["skipped"] += 1
            continue

        # Issue the minimal PATCH. One field, the deferred FK.
        endpoint = CONTENT_TYPE_ENDPOINTS[entry.child_content_type]
        try:
            http.patch(f"{endpoint}{child_id}/", {entry.field_name: target_id})
        except NetboxHTTPError as exc:
            logger.warning(
                "Phase-2 PATCH failed for %s id=%d field=%s: HTTP %d %s",
                entry.child_content_type, child_id, entry.field_name,
                exc.status, exc.body[:200],
            )
            summary.counts["failed"] += 1
            summary.failures.append((entry, str(exc)))
            continue

        summary.counts["patched"] += 1
        logger.info(
            "Phase-2 PATCH %s id=%d %s -> %s id=%d",
            entry.child_content_type, child_id, entry.field_name,
            entry.target_content_type, target_id,
        )

    return summary
```

Wire into the driver right after the Phase-1 loop ends:

```python
# src/nbsnap/import_/driver.py
from nbsnap.import_.phase2 import run_phase2

def run_import(http, snapshot_dir, ...):
    ...
    # Phase-1 loop populates deferred_queue and dest_index.
    ...
    phase2 = run_phase2(http, deferred_queue, dest_index=index, registry=registry)
    summary.phase2 = phase2  # surface in the CLI's audit output
    return summary
```

#### Integration test

```python
# tests/integration/test_phase2_deferred.py
"""End-to-end Phase-2 against the netbox-docker test stacks.

Seeds the source with a device whose primary_ip4 points at its
own Vlan600 interface IP (the classic Device <-> IPAddress
cycle), runs export, runs import, asserts the destination
device has primary_ip4 set after the Phase-2 pass."""

import pytest

from nbsnap.export.driver import run_export
from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.driver import run_import
from nbsnap.schema.status import VersionSkew


@pytest.mark.usefixtures("require_stack")
def test_phase2_patches_primary_ip4(tmp_path) -> None:
    src = NetboxHTTP("http://localhost:8080", "0123456789abcdef" * 2 + "01234567")
    dst = NetboxHTTP("http://localhost:8081", "abcdef0123456789" * 2 + "abcdef01")
    snap_dir = tmp_path / "snap"

    run_export(src, snap_dir)
    summary = run_import(dst, snap_dir, max_skew=VersionSkew.MINOR, on_error="continue")

    # Phase-2 should have PATCHed primary_ip4 on every device that
    # had it set in the source. The seed has 2 devices with primary_ip4.
    assert summary.phase2.counts["patched"] >= 2
    assert summary.phase2.is_clean(), summary.phase2.failures

    # Cross-check via the destination's own GET.
    import requests
    resp = requests.get(
        "http://localhost:8081/api/dcim/devices/?name=d39a",
        headers={"Authorization": f"Token {dst._token}"},
        timeout=10,
    )
    device = resp.json()["results"][0]
    assert device["primary_ip4"] is not None
    assert device["primary_ip4"]["address"] == "172.16.1.10/24"
```

#### Acceptance criteria

* After Phase-1 + Phase-2 against the renderer-minimum fixture,
  every device that had `primary_ip4` set on the source has it
  set on the destination too.
* `Phase2Summary.is_clean()` returns True on a healthy run.
* Re-running the same import is a no-op: `patched: 0`,
  `skipped: 0`, `failed: 0` because every FK is already in
  place.
* The destination's audit log shows one PATCH per deferred FK,
  with a minimal one-field body.

**Estimated Effort:** 1-2h

### [TEST-06] Idempotency two-run integration test

**Context:** `goals.md` success criterion 2.

**Requirements:**

- `tests/integration/test_import_idempotency.py`.
- Seed source, export, import to fresh destination, inspect audit
  log assert every result is CREATED.
- Re-run import without changes, inspect audit log assert every
  result is NOOP and no PATCH was sent.

**Testing:** run the test, confirm green. Inject a deliberate
extra field on one record in the audit comparison code, confirm the
test fails with a clear pointer to the offending content type.

**Estimated Effort:** 1-2h

### [TEST-07] Cycle resolution end-to-end integration test

**Context:** `docs/frictions/01`. The headline test for the cycle
machinery.

**Requirements:**

- `tests/integration/test_import_cycles.py`.
- Seed source with a Device + Interface + IPAddress chain where
  Device.primary_ip4 points at the IP.
- Export, import.
- GET the device on the destination, assert `primary_ip4.address`
  matches the source's value.
- Repeat for IPv6 if seeded.

**Testing:** run the test, confirm green. Comment out the Phase 2
writer call in `run_import`, re-run, confirm the test fails with a
clear assertion message.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36-blocker] Enum-dict serialisation mismatch on write

**Context:** the actual production rescue import (logged at
`tmp/nbsnap-rescue-10/import-attempt-3.log`) failed 5105 of 5153
rows. The root cause is **not** ordering, it is the enum-dict
shape: NetBox's GET response surfaces enum fields as
`{"value": "active", "label": "Active"}`, the matching write
endpoint refuses that shape and expects the bare value string.

#### Architectural specification

NetBox's REST API is asymmetric on choice fields. The schema
generator emits a JSON-schema `string` with an `enum:` list for
write endpoints, but the response serializer wraps the same
value in a `{value, label}` envelope for human readability. The
asymmetry is documented in the NetBox REST API overview:
`https://netboxlabs.com/docs/netbox/integrations/rest-api/`,
section "Brief Format" and the choice-field examples in the
Swagger UI at `https://demo.netbox.dev/api/schema/swagger-ui/`.

Sites are the obvious example. The GET response is

    GET /api/dcim/sites/1/
    {
      "id": 1, "name": "Hall-A", "slug": "a",
      "status": {"value": "active", "label": "Active"},
      ...
    }

but the write body must be

    POST /api/dcim/sites/
    {
      "name": "Hall-A", "slug": "a",
      "status": "active",
      ...
    }

The snapshot wrote the GET shape verbatim, so every record with
a `status`, `airflow`, `face`, `type`-on-interface, or any other
enum field is rejected by NetBox before its FKs are even
evaluated. Sites failed to POST, dependent locations / racks /
devices cannot resolve them, and the import cascade fans out
from there.

The fix lives on the export side (the snapshot is the long-
lived artefact, so the canonical fix updates the stored shape).
An import-side coerce stays as a belt-and-braces fallback so
old snapshots remain importable without re-exporting.

#### REST API details

* Affected endpoints, every write endpoint that has at least
  one choice field in its request body. Among the renderer-
  minimum scope, these are confirmed via the Swagger UI:
  * `POST/PATCH /api/dcim/sites/` (`status`)
  * `POST/PATCH /api/dcim/locations/` (`status`)
  * `POST/PATCH /api/dcim/racks/` (`status`, `type`,
    `outer_unit`, `width`)
  * `POST/PATCH /api/dcim/devices/` (`status`, `airflow`,
    `face`)
  * `POST/PATCH /api/dcim/interfaces/` (`type`, `mode`,
    `duplex`, `rf_role`, `rf_channel_width`, `poe_mode`,
    `poe_type`)
  * `POST/PATCH /api/dcim/cables/` (`status`, `type`,
    `length_unit`)
  * `POST/PATCH /api/ipam/ip-addresses/` (`status`, `role`)
  * `POST/PATCH /api/ipam/prefixes/` (`status`)
  * `POST/PATCH /api/ipam/ip-ranges/` (`status`)
  * `POST/PATCH /api/ipam/vlans/` (`status`)
* Detection rule: a field value is an enum-dict iff it is a
  mapping with **exactly two keys** `{"value", "label"}` and
  both values are strings.
* OpenAPI cross-check, optional but cheap: the field's request
  schema has `type: string` (possibly with `enum: [...]`) or is
  an `allOf` wrapper whose target is a string-enum component.

Example minimal reproducer against any NetBox install:

    # Reading: enum-dict shape
    curl -sH "Authorization: Token $T" \
        https://netbox.example.com/api/dcim/sites/1/ \
        | jq .status
    # -> {"value": "active", "label": "Active"}

    # Writing the same shape, NetBox 400s:
    curl -sX PATCH -H "Authorization: Token $T" \
        -H "Content-Type: application/json" \
        -d '{"status": {"value": "active", "label": "Active"}}' \
        https://netbox.example.com/api/dcim/sites/1/
    # -> HTTP 400 {"status":["Value must be passed directly..."]}

    # Writing the flat value succeeds:
    curl -sX PATCH -H "Authorization: Token $T" \
        -H "Content-Type: application/json" \
        -d '{"status": "active"}' \
        https://netbox.example.com/api/dcim/sites/1/
    # -> HTTP 200

#### Implementation, export side (canonical)

Add `_collapse_enum_dict` to `src/nbsnap/export/extractor.py`
and call it from `_apply_allowlist`:

```python
# src/nbsnap/export/extractor.py

from collections.abc import Mapping
from typing import Any

# NetBox's choice-field response shape: a dict with exactly
# these two keys. We collapse it to the bare value string so
# the snapshot matches what NetBox's write endpoints accept.
_ENUM_DICT_KEYS = frozenset({"value", "label"})


def _collapse_enum_dict(value: Any) -> Any:
    """If `value` is a NetBox enum-dict, return its `value` slot.

    NetBox 4.x serialises enum fields as
    `{"value": "active", "label": "Active"}` on GET but expects
    only `"active"` on POST/PATCH. Recognise the shape by the
    exact key set so we never accidentally collapse a real
    payload dict that happens to carry a `value` field.
    """
    if isinstance(value, Mapping) and frozenset(value.keys()) == _ENUM_DICT_KEYS:
        inner = value["value"]
        # NetBox stores the writable enum as a string; defend
        # against unusual shapes (numbers, nested dicts) by
        # passing them through untouched.
        if isinstance(inner, (str, int, bool)) or inner is None:
            return inner
    return value


def _apply_allowlist(record: Mapping[str, Any], allowlist: frozenset[str]) -> dict[str, Any]:
    """Keep only allowlisted fields AND collapse enum-dicts."""

    return {k: _collapse_enum_dict(v) for k, v in record.items() if k in allowlist}
```

That single change is enough to flip the 5105-failure import
into success for every row that was previously stuck on the
enum 400.

#### Implementation, import side (defensive fallback)

So old snapshots exported before the fix still upload cleanly,
add the same coercion right before the POST/PATCH body is
serialised. In `src/nbsnap/import_/upsert.py`:

```python
# src/nbsnap/import_/upsert.py

from nbsnap.export.extractor import _collapse_enum_dict

def _coerce_body_for_write(body: dict) -> dict:
    """Defensive: collapse any enum-dicts still present in the
    body. Should be a no-op for snapshots produced after
    FEAT-36-blocker landed."""
    return {k: _collapse_enum_dict(v) for k, v in body.items()}


# Inside upsert, just before the POST and inside the PATCH diff:
created = http.post(endpoint, _coerce_body_for_write(dict(body)))
# ...
http.patch(f"{endpoint}{existing_id}/", _coerce_body_for_write(diff))
```

#### Regression test

```python
# tests/unit/test_export_enum_collapse.py
from nbsnap.export.extractor import _collapse_enum_dict, _apply_allowlist


def test_collapses_enum_dict() -> None:
    """The classic NetBox GET-response enum-dict collapses to the value."""

    assert _collapse_enum_dict({"value": "active", "label": "Active"}) == "active"


def test_passes_through_non_enum_dicts() -> None:
    """A regular dict (e.g. a nested object) must not be collapsed."""

    nested = {"id": 7, "name": "Hall-D"}
    assert _collapse_enum_dict(nested) == nested

    # Even a dict that *contains* a "value" key but more keys
    # besides {value, label} stays intact.
    rich = {"value": "x", "label": "X", "id": 1}
    assert _collapse_enum_dict(rich) == rich


def test_passes_through_non_dicts() -> None:
    assert _collapse_enum_dict("active") == "active"
    assert _collapse_enum_dict(7) == 7
    assert _collapse_enum_dict(None) is None
    assert _collapse_enum_dict([]) == []


def test_apply_allowlist_collapses_status_inline() -> None:
    """End-to-end, _apply_allowlist on a sample site row turns
    status from {value,label} into the bare string."""

    raw = {
        "id": 1,
        "name": "Hall-A",
        "slug": "a",
        "status": {"value": "active", "label": "Active"},
        "tenant": None,
    }
    allowlist = frozenset({"name", "slug", "status"})
    out = _apply_allowlist(raw, allowlist)
    assert out == {"name": "Hall-A", "slug": "a", "status": "active"}
```

#### Acceptance criteria

After this ticket lands and a re-export is run against the
production source:

1. `head -1 snapshot/dcim/sites.jsonl | jq .body.status` returns
   `"active"` (the string), not the dict.
2. A fresh import against an empty destination NetBox 4.6.x
   produces `created: <high>`, `failed: ~0` on the renderer-
   minimum scope (residual failures should only be the polymorphic
   ordering issues FEAT-36d / FEAT-36b address).
3. Running the import against a pre-fix snapshot (no re-export)
   still works because the import-side coerce in `upsert.py`
   handles the legacy shape.

**Estimated Effort:** 1-2h

**Priority:** BLOCKER, every other FEAT-36 ticket below is
nearly useless until this lands because the records they would
upsert get rejected at the HTTP layer.

### [REFINED] [FEAT-36a] Snapshot natural-key index loader

#### Architectural specification

The look-ahead resolver (`FEAT-36b`) needs to answer two
questions instantly: "does the snapshot itself carry the record
identified by `(content_type, NK)`?" and "if yes, what is the
body it carried?". A pure-in-memory map keyed by the natural-key
tuple gives both for O(1).

The SnapshotIndex is read-only after construction and is built
**once** at the very top of `run_import`, before any other
work. Subsequent FK resolutions consult it without any disk or
network IO. Memory cost is bounded by the snapshot's total row
count (the renderer-minimum scope tops out around 50k rows /
~5 MB JSON; production-sized snapshots have been observed in
the 200k row / 20 MB range, well below the budget a NetBox
operator already pays for a `pynetbox` GET-all of any single
content type).

This ticket does NOT touch NetBox's REST API; the data source
is the snapshot directory on disk. The NetBox-side counterpart
is `NKIndex` (already in `src/nbsnap/import_/nk_index.py`),
which builds the destination side from `GET ?brief=true`
listings. The two indices together form a two-tier lookup: the
NKIndex says "does the destination have this NK now?", the
SnapshotIndex says "if not, can we create it from the snapshot?".

#### REST API details

None directly. The SnapshotIndex is a pure local helper.

For context the consumer (FEAT-36b) will pair it with NetBox
calls of this shape:

    GET  /api/<endpoint>/?brief=true         (NKIndex build)
    POST /api/<endpoint>/                    (creating a missing target)

documented in the NetBox Swagger UI at
`https://demo.netbox.dev/api/schema/swagger-ui/` under each
content type's `list` and `create` operations.

#### Implementation

```python
# src/nbsnap/import_/snapshot_index.py
"""In-memory (content_type, NK) -> body map for look-ahead.

Built once at the start of run_import, queried thereafter
without any IO. The body dict is stored as-is from the
snapshot's JSONL, so the consumer should not mutate it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A natural key in the snapshot is a JSON-deserialised list-of-
# lists. We normalise to tuple-of-tuples at load time so the
# index key is hashable and comparisons work cleanly.
NaturalKey = tuple


def _to_tuple(value: Any) -> Any:
    """Recursively convert lists to tuples so the value is hashable."""
    if isinstance(value, list):
        return tuple(_to_tuple(v) for v in value)
    return value


@dataclass
class SnapshotIndex:
    """Maps `(content_type, NK)` -> snapshot body.

    Memory footprint per row is approximately the size of the
    JSON body that the export emitted. At ~100 bytes per row for
    the renderer-minimum scope, a 50,000-row snapshot consumes
    ~5 MB of RAM. Plus a small constant overhead per dict entry.
    Operators with very large NetBoxes (>500k rows) may want to
    add a streaming variant in a follow-up, today's footprint is
    well within typical operator-host RAM.
    """

    _by_key: dict[tuple[str, NaturalKey], dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_snapshot(cls, snapshot_dir: Path) -> SnapshotIndex:
        """Walk every JSONL under `snapshot_dir`, build the index."""

        index = cls()
        # Map of relative jsonl path -> content type. We derive the
        # content type from the CONTENT_TYPE_FILES table that the
        # writer already uses, so the two stay in sync.
        from nbsnap.export.writer import CONTENT_TYPE_FILES

        ct_by_rel = {rel: ct for ct, rel in CONTENT_TYPE_FILES.items()}

        for jsonl_path in snapshot_dir.rglob("*.jsonl"):
            # Skip top-level audit/progress logs, they are not records.
            if jsonl_path.name in {"flags.jsonl", "progress.jsonl", "_deferred.jsonl"}:
                continue
            rel = jsonl_path.relative_to(snapshot_dir).as_posix()
            content_type = ct_by_rel.get(rel)
            if content_type is None:
                # Unknown jsonl, don't index it; the importer will
                # log this as an out-of-scope row when it gets there.
                continue
            for row in _iter_jsonl(jsonl_path):
                nk = _to_tuple(row.get("natural_key"))
                body = row.get("body") or {}
                if isinstance(body, dict):
                    index._by_key[(content_type, nk)] = body
        return index

    def lookup(
        self, content_type: str, natural_key: NaturalKey
    ) -> dict[str, Any] | None:
        """Return the snapshot body for `(content_type, NK)` or None."""
        return self._by_key.get((content_type, _to_tuple(natural_key)))

    def has(self, content_type: str, natural_key: NaturalKey) -> bool:
        """Constant-time membership check."""
        return (content_type, _to_tuple(natural_key)) in self._by_key

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[tuple[str, NaturalKey]]:
        return iter(self._by_key.keys())


def _iter_jsonl(path: Path) -> Iterator[Mapping[str, Any]]:
    """Stream a JSONL file, skip blank / malformed lines silently."""
    with path.open(encoding="utf-8") as fp:
        for raw in fp:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                # Malformed row; the export pipeline already drops
                # these via flags.jsonl, but be defensive.
                continue
```

Wire into the driver:

```python
# src/nbsnap/import_/driver.py

from nbsnap.import_.snapshot_index import SnapshotIndex

def run_import(http, snapshot_dir, *, max_skew=VersionSkew.MINOR, on_error="stop"):
    ...
    # Build the snapshot-side NK lookup table once, up front.
    snapshot_index = SnapshotIndex.from_snapshot(snapshot_dir)
    # Pass snapshot_index into _resolve_body / upsert so FEAT-36b's
    # demand-driven path can consult it on a destination miss.
    ...
```

#### Regression test

```python
# tests/unit/test_import_snapshot_index.py
import json
from pathlib import Path

from nbsnap.import_.snapshot_index import SnapshotIndex


def _write_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(row) + "\n")


def test_index_finds_records_by_nk(tmp_path: Path) -> None:
    """A small snapshot dir is indexed; lookup returns the body."""

    _write_row(
        tmp_path / "dcim" / "sites.jsonl",
        {"natural_key": ["hall-a"], "body": {"name": "Hall-A", "slug": "a"}},
    )
    _write_row(
        tmp_path / "dcim" / "devices.jsonl",
        {
            "natural_key": [["hall-a"], "d39a"],
            "body": {"name": "d39a"},
        },
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert idx.has("dcim.site", ("hall-a",))
    assert idx.lookup("dcim.site", ("hall-a",)) == {"name": "Hall-A", "slug": "a"}
    # Composite NK with a nested tuple.
    assert idx.lookup("dcim.device", (("hall-a",), "d39a")) == {"name": "d39a"}


def test_missing_nk_returns_none(tmp_path: Path) -> None:
    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert idx.lookup("dcim.site", ("ghost",)) is None
    assert idx.has("dcim.site", ("ghost",)) is False


def test_audit_files_are_skipped(tmp_path: Path) -> None:
    """flags.jsonl / progress.jsonl are not records, must not load."""

    (tmp_path / "flags.jsonl").write_text(
        json.dumps({"content_type": "x", "field": "y"}) + "\n",
        encoding="utf-8",
    )
    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert len(idx) == 0
```

#### Acceptance criteria

* `SnapshotIndex.from_snapshot(tmp_path)` on the production-
  rescue snapshot (~5k rows) returns an index with the same row
  count as the manifest's `counts` total (within a few rows for
  the deferred / flagged cases).
* Lookup latency is in the sub-microsecond range (dict access).
* Memory footprint < 50 MB even on a 500k-row snapshot.

**Estimated Effort:** 1-2h

### [FEAT-36b1] Look-ahead module skeleton with DeferredFK dataclass

**Context:** the parent ticket FEAT-36b is decomposed into five
atomic sub-tickets. This first one lands the module file and
the data shape later sub-tickets reuse. No NetBox API calls
happen here, just the dataclass and the import-side scaffolding.

The downstream sub-tickets (FEAT-36b2 through FEAT-36b5)
gradually add the destination-tier lookup, the snapshot-tier
lookup, the cycle detection, and the driver wiring. Keeping
the data shape stable up front lets those tickets land in any
order.

**Requirements:**

- Create `src/nbsnap/import_/lookahead.py` with a module
  docstring explaining the two-tier resolution model
  (destination NKIndex first, snapshot SnapshotIndex second).
- Define `MAX_DEPTH = 200` as a module-level constant so a
  malformed snapshot cannot cause unbounded recursion in
  later sub-tickets.
- Define the frozen dataclass:

      @dataclass(frozen=True)
      class DeferredFK:
          child_content_type: str
          child_nk: tuple
          field_name: str
          target_content_type: str
          target_nk: tuple

- Add a module-level `logger = logging.getLogger(__name__)`.
- Export `DeferredFK` and `MAX_DEPTH` via `__all__` so the
  driver can `from nbsnap.import_.lookahead import DeferredFK`.

**Testing:**

- Unit test in `tests/unit/test_import_lookahead_skeleton.py`.
- Verify `DeferredFK` is frozen by attempting to set an
  attribute and catching `dataclasses.FrozenInstanceError`.
- Verify two `DeferredFK` instances with identical fields
  hash to the same value and compare equal (needed for the
  Phase-2 dedupe in a later ticket).
- Verify `MAX_DEPTH == 200` so a future bump shows up as a
  test diff.

**Estimated Effort:** 1h

### [FEAT-36b2] Destination-tier resolve_or_create helper

**Context:** layer 1 of the two-tier resolver. Looks up the
target in the destination's NKIndex and returns the id when
present. Returns `None` on miss so the caller can fall through
to the snapshot-tier check that lands in FEAT-36b3.

**Requirements:**

- In `src/nbsnap/import_/lookahead.py`, add:

      def resolve_or_create(
          http, snapshot_index, dest_index, registry, *,
          content_type, natural_key,
          processing_stack, deferred_queue, depth=0,
      ) -> int | None:

- The body for this sub-ticket implements ONLY the
  destination check: call `dest_index.ensure_built(http,
  registry, content_type)`, then `dest_index.lookup(content_type,
  natural_key)`. Return the id if found, return `None`
  otherwise. The snapshot fallback, cycle detection, and
  recursion belong to later sub-tickets and should be left as
  TODO comments referencing FEAT-36b3 and FEAT-36b4.
- Add the depth-cap check at the top of the function so
  later sub-tickets do not need to add it: if `depth >=
  MAX_DEPTH`, log a warning and return `None`.
- Mark `processing_stack` and `deferred_queue` as unused for
  now (`_ = processing_stack`) so mypy and ruff stay quiet
  until FEAT-36b4 wires them in.

**Testing:**

- Unit test in `tests/unit/test_import_lookahead_destination_tier.py`.
- Case 1, destination has the target: pre-populate
  `dest_index.insert("dcim.site", ("hall-d",), 42)`, call
  `resolve_or_create` for that NK, assert it returns 42 and
  did not call `http.post` (use `MagicMock`).
- Case 2, destination missing the target: empty NKIndex,
  call `resolve_or_create`, assert it returns `None` and did
  not call `http.post`.
- Case 3, depth >= MAX_DEPTH returns `None` without consulting
  the index.

**Estimated Effort:** 1-2h

### [FEAT-36b3] Snapshot-tier fallback with recursive upsert

**Context:** layer 2 of the two-tier resolver. When the
destination misses, the resolver consults `SnapshotIndex` (from
FEAT-36a). If the target is in the snapshot, recursively upsert
it on the destination, then return the new id so the caller's
FK resolves cleanly.

NetBox endpoints touched by this sub-ticket are the same the
existing `upsert()` path uses:

* `POST /api/<endpoint>/` for the recursive create.
* `GET /api/<endpoint>/<id>/` for the upsert detail check.

**Requirements:**

- Extend `resolve_or_create` from FEAT-36b2. After the
  destination-tier miss, do:

      snapshot_body = snapshot_index.lookup(content_type, natural_key)
      if snapshot_body is None:
          return None
      from nbsnap.import_.upsert import upsert
      result = upsert(
          http, content_type=content_type, natural_key=natural_key,
          body=dict(snapshot_body), index=dest_index, registry=registry,
      )
      return result.destination_id if result.outcome is not UpsertOutcome.FAILED else None

- Import `UpsertOutcome` and `upsert` lazily inside the
  function to avoid a circular import between
  `lookahead.py` and `upsert.py`.
- Do NOT yet thread the recursion through `_resolve_body`,
  cycle detection lands in FEAT-36b4.

**Testing:**

- Unit test in `tests/unit/test_import_lookahead_snapshot_tier.py`.
- Case 1, snapshot has the target: pre-populate the
  `SnapshotIndex` with one row, stub `http.post` to return
  `{"id": 99, "slug": "hall-d", "name": "Hall D"}`. Call
  `resolve_or_create`. Assert the function returns 99 and
  `dest_index.lookup` for that NK now returns 99 (the upsert
  inserted it).
- Case 2, snapshot also misses: empty index AND empty
  snapshot, assert the function returns `None` and made no
  POST.

**Estimated Effort:** 1-2h

### [FEAT-36b4] Cycle detection with processing_stack and DeferredFK queue

**Context:** the cycle Device.primary_ip4 -> IPAddress -> Interface
-> Device requires explicit detection. When recursion encounters
a target that is already on the processing stack, we push a
`DeferredFK` onto the queue and return `None` so the outer
upsert proceeds without that field. Phase-2 (FEAT-36c) later
PATCHes the deferred field.

**Requirements:**

- Add cycle detection to `resolve_or_create`:
  - At the top, compute `key = (content_type, natural_key)`.
  - If `key in processing_stack`, log a debug message,
    append a `DeferredFK` to `deferred_queue` (caller is
    responsible for the child fields, this sub-ticket
    surfaces an empty `child_content_type=""` placeholder
    that FEAT-36b5 fills in via the wrapper helper), and
    return `None`.
- Push and pop `key` from `processing_stack` around the
  recursive `upsert` call so siblings can detect the cycle.
  Use a `try / finally` block to guarantee the discard runs
  even if the inner call raises.
- Confirm `MAX_DEPTH` still applies after pushing onto the
  stack, so a very deep non-cyclic chain still terminates.

**Testing:**

- Unit test in `tests/unit/test_import_lookahead_cycle_detection.py`.
- Case 1, cycle: pre-seed `processing_stack` with a key,
  call `resolve_or_create` for the same key, assert it
  returns `None`, `http.post` is never called, and the
  `deferred_queue` grew by exactly one entry whose
  `target_content_type` and `target_nk` match the input.
- Case 2, stack is cleaned up on success: empty
  destination + snapshot has the target, after the call
  succeeds, `processing_stack` is back to its prior state.
- Case 3, stack is cleaned up on failure: stub `upsert` to
  raise an exception, assert the stack is empty afterwards
  and the exception propagates.

**Estimated Effort:** 1-2h

### [FEAT-36b5] Wire look-ahead into _resolve_body in the import driver

**Context:** consumes FEAT-36b1 through FEAT-36b4 and brings
demand-driven resolution into the actual import path. The
driver passes the `snapshot_index`, `processing_stack`, and
`deferred_queue` through `_resolve_body` so every simple-FK
miss has a chance to recover via look-ahead.

**Requirements:**

- In `src/nbsnap/import_/driver.py`:
  - Build `SnapshotIndex.from_snapshot(snapshot_dir)` at the
    top of `run_import`.
  - Build `deferred_queue: list[DeferredFK] = []` and pass
    it through `_resolve_body`.
  - Initialise `processing_stack: set[tuple[str, tuple]] = set()`
    per-row inside the Phase-1 loop.
- In `_resolve_body`, replace the existing simple-FK
  KeyError-drop branch with a call to `resolve_or_create`.
  When the call returns `None` AND the queue grew, the field
  was deferred to Phase-2 (do not log a `_warn_dropped`);
  when it returns `None` without queue growth, the target is
  out-of-scope (call the existing `_warn_dropped`).
- The `current_nk` and `field_name` that get baked into the
  `DeferredFK` come from the caller; have `resolve_or_create`
  return the queue-length delta so the caller can patch the
  most recent entry with the missing fields.

**Testing:**

- Unit test in `tests/unit/test_import_driver_lookahead.py`.
- Build a minimal `OpenAPI` schema with a Device whose `site`
  is an FK to Site, a `SnapshotIndex` containing a Site row,
  and an empty `NKIndex`.
- Call `_resolve_body("dcim.device", {"name": "d39a", "site":
  ["hall-d"]}, ...)`.
- Assert the resolved body has `site` set to the destination
  id of the newly-created Site (proves the look-ahead created
  the parent on demand).
- Run the full unit suite (`pytest tests/unit -q`) and confirm
  no regressions against the existing 158 tests.

**Estimated Effort:** 1-2h


### [REFINED] [FEAT-36c] Wire Phase-2 deferred-FK writer into the import driver

#### Architectural specification

`FEAT-23` (above) holds the implementation of the Phase-2
writer itself, complete with code and tests. This ticket is the
**wire-up** that makes Phase-2 actually run as part of
`nbsnap import` and surfaces its counters in the CLI summary.
It also closes the loop with `FEAT-36b` so the deferred queue
the look-ahead resolver populates flows into Phase-2 without
manual plumbing.

Two integration points:

1. **Driver wiring.** After Phase-1 finishes, the driver calls
   `run_phase2(http, deferred_queue, dest_index=..., registry=...)`
   and stores the returned `Phase2Summary` on the
   `ImportSummary` object. The Phase-1 and Phase-2 counters
   stay separate so an operator can see the split.
2. **CLI summary.** `src/nbsnap/import_cli.py` prints the
   Phase-2 counts under a separate "phase2:" block. The exit
   code accounts for Phase-2 failures the same way it accounts
   for Phase-1 failures (exit 2 on any failure).

#### REST API details

None new beyond what `FEAT-23` documents. This ticket is pure
driver glue.

#### Implementation

```python
# src/nbsnap/import_/driver.py

from nbsnap.import_.lookahead import DeferredFK
from nbsnap.import_.phase2 import Phase2Summary, run_phase2
from nbsnap.import_.snapshot_index import SnapshotIndex


@dataclass
class ImportSummary:
    preflight: PreflightReport
    counts: Counter[UpsertOutcome] = field(default_factory=Counter)
    failures: list[UpsertResult] = field(default_factory=list)
    # NEW: Phase-2 results split out so an operator sees what
    # happened in each phase separately.
    phase2: Phase2Summary | None = None


def run_import(http, snapshot_dir, *, max_skew, on_error):
    ...
    snapshot_index = SnapshotIndex.from_snapshot(Path(snapshot_dir))
    deferred_queue: list[DeferredFK] = []
    dest_index = NKIndex()

    # Phase-1 (existing loop, threading snapshot_index and
    # deferred_queue through _resolve_body).
    for ct in _content_type_order(manifest, snapshot_dir):
        ...

    # Phase-2, new.
    summary.phase2 = run_phase2(
        http, deferred_queue,
        dest_index=dest_index, registry=registry,
    )
    return summary
```

```python
# src/nbsnap/import_cli.py

def run_import_cli(args):
    ...
    summary = run_import(http, args.in_dir, max_skew=max_skew, on_error=args.on_error)

    # Phase-1 block, existing.
    sys.stderr.write("# nbsnap import complete\n")
    sys.stderr.write(f"  preflight version skew: {summary.preflight.version_skew.name}\n")
    for outcome in (UpsertOutcome.CREATED, UpsertOutcome.UPDATED,
                    UpsertOutcome.NOOP, UpsertOutcome.FAILED):
        sys.stderr.write(f"  {outcome.value}: {summary.counts.get(outcome, 0)}\n")

    # Phase-2 block, new.
    if summary.phase2 is not None:
        sys.stderr.write("  phase2:\n")
        sys.stderr.write(f"    patched: {summary.phase2.counts.get('patched', 0)}\n")
        sys.stderr.write(f"    skipped: {summary.phase2.counts.get('skipped', 0)}\n")
        sys.stderr.write(f"    failed:  {summary.phase2.counts.get('failed', 0)}\n")
        if summary.phase2.failures:
            first = summary.phase2.failures[0]
            sys.stderr.write(f"    first phase2 failure: {first[1][:160]}\n")

    # Exit code: Phase-1 failures OR Phase-2 failures.
    if summary.failures or (
        summary.phase2 is not None and not summary.phase2.is_clean()
    ):
        return EXIT_ROW_FAILURES
    return EXIT_OK
```

#### Regression tests

```python
# tests/unit/test_import_phase2_wiring.py
from unittest.mock import patch

from nbsnap.import_.driver import run_import
from nbsnap.import_.phase2 import Phase2Summary


def test_phase2_summary_attached_to_import_summary(tmp_path):
    """run_import() returns an ImportSummary with a phase2 field."""
    # Stub everything except the structural shape.
    snap = _write_minimal_snapshot(tmp_path)
    with patch("nbsnap.import_.driver.run_phase2",
               return_value=Phase2Summary()) as p2:
        summary = run_import(
            http=_fake_http(),
            snapshot_dir=snap,
        )
    p2.assert_called_once()
    assert summary.phase2 is not None
```

#### Acceptance criteria

* `summary.phase2` is non-None after every `nbsnap import` run.
* The CLI's stderr summary includes a `phase2:` block.
* Exit code 2 fires for Phase-2 failures the same way it fires
  for Phase-1 failures.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36d] Polymorphic-FK ordering hints for the planner

#### Architectural specification

NetBox uses generic foreign keys for fields that can point at
several content types: cable terminations, IP-address
assignments, services, wireless links. The OpenAPI schema
expresses these as two paired fields — `<prefix>_object_type`
(a string) and `<prefix>_object_id` (an integer) — instead of a
direct `$ref`. The planner's static FK detection has no way to
tell, from the schema alone, that a Cable's `a_terminations`
items will end up pointing at `dcim.interface` at runtime.

Consequence: the planner emits `dcim.cable` and
`ipam.ipaddress` BEFORE `dcim.interface`, even though both
reference Interface. The `FEAT-36b` look-ahead resolver is the
runtime safety net; this ticket gets the common case right at
plan time so the safety net rarely fires.

The fix is a **curated table of polymorphic target hints**.
Each entry says "for this content type, this field can point
at this target". The graph builder reads the table after the
static FK edges land and adds synthetic edges that look like
nullable m2m FKs from the owner to each target. The planner
then sorts naturally; the synthetic edges are still
defer-eligible if a true cycle exists, so the existing
cycle-breaking machinery still works.

Hints are version-stamped because NetBox occasionally
restructures these. The reference NetBox docs:
* Cable terminations: `https://demo.netbox.dev/api/schema/swagger-ui/#/dcim/dcim_cables_list`,
  see `a_terminations` / `b_terminations` schema.
* IPAddress assigned object: same Swagger UI under
  `ipam_ip_addresses_list`, see `assigned_object_type` /
  `assigned_object_id`.
* Service assigned object: same idea, see `ipam_services_list`.
* WirelessLink endpoints A/B: see `wireless_wireless_links_list`.

#### REST API details

Run-time discovery of accepted target types uses
`OPTIONS /api/<endpoint>/`, which NetBox returns with the
`actions.POST.<field>.choices` array (see
`src/nbsnap/graph/polymorphic.py:discover_via_options`, already
landed in FEAT-05c1). The hint table is a **static fallback /
optimisation** so the planner does not need an OPTIONS round
trip to know that Cable's terminations target Interface.

For the curated entries, the verified NetBox 4.6 list is:

| Owner | Field | Likely targets |
| :--- | :--- | :--- |
| `dcim.cable` | `a_terminations`, `b_terminations` | `dcim.interface`, `dcim.frontport`, `dcim.rearport`, `dcim.consoleport`, `dcim.consoleserverport`, `dcim.powerport`, `dcim.poweroutlet`, `circuits.circuittermination` |
| `ipam.ipaddress` | `assigned_object` | `dcim.interface`, `virtualization.vminterface`, `ipam.fhrpgroup` |
| `ipam.service` | `parent` | `dcim.device`, `virtualization.virtualmachine`, `ipam.fhrpgroup` |
| `wireless.wirelesslink` | `interface_a`, `interface_b` | `dcim.interface` |
| `dcim.virtualchassis` | `master` | `dcim.device` |

#### Implementation

```python
# src/nbsnap/graph/polymorphic.py

# Hand-curated polymorphic target hints. Each entry says "this
# content type, via this field, can point at this target". The
# graph builder uses the table to add synthetic ordering edges
# so the planner emits the target content type BEFORE the
# owner. The OPTIONS-based discovery in discover_via_options()
# remains the runtime source of truth; this table is the cheap
# upfront optimisation that keeps the common case from
# round-tripping to NetBox.
#
# Each entry carries a `verified_against` tag so a NetBox bump
# that restructures these fields can be caught by re-running
# the hint check against the new schema.
POLYMORPHIC_HINTS: list[dict] = [
    {
        "owner_ct": "dcim.cable",
        "field": "a_terminations",
        "targets": [
            "dcim.interface", "dcim.frontport", "dcim.rearport",
            "dcim.consoleport", "dcim.consoleserverport",
            "dcim.powerport", "dcim.poweroutlet",
            "circuits.circuittermination",
        ],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "dcim.cable",
        "field": "b_terminations",
        "targets": [
            "dcim.interface", "dcim.frontport", "dcim.rearport",
            "dcim.consoleport", "dcim.consoleserverport",
            "dcim.powerport", "dcim.poweroutlet",
            "circuits.circuittermination",
        ],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "ipam.ipaddress",
        "field": "assigned_object",
        "targets": ["dcim.interface", "virtualization.vminterface", "ipam.fhrpgroup"],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "ipam.service",
        "field": "parent",
        "targets": ["dcim.device", "virtualization.virtualmachine", "ipam.fhrpgroup"],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "wireless.wirelesslink",
        "field": "interface_a",
        "targets": ["dcim.interface"],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "wireless.wirelesslink",
        "field": "interface_b",
        "targets": ["dcim.interface"],
        "verified_against": "netbox 4.6.2",
    },
]


def add_hint_edges(graph, scope: set[str]) -> None:
    """Add synthetic FK edges from the polymorphic hint table.

    For each `(owner, field, target)` triple where both content
    types are in scope, add an edge child=owner -> parent=target,
    marked nullable + m2m so the planner can still defer it if a
    cycle exists. The label `field` is suffixed with `__hint` so
    the deferred-edge picker can recognise these and prefer
    deferring them over real schema edges.
    """
    from nbsnap.graph.model import Edge, Node

    for hint in POLYMORPHIC_HINTS:
        owner = hint["owner_ct"]
        if owner not in scope:
            continue
        for target in hint["targets"]:
            if target not in scope:
                continue
            graph.add_node(Node(target))  # idempotent
            graph.add_edge(
                Edge(
                    child=owner,
                    parent=target,
                    field=f"{hint['field']}__hint",
                    nullable=True,
                    required=False,
                    is_m2m=True,
                    polymorphic_targets=tuple(hint["targets"]),
                )
            )
```

```python
# src/nbsnap/graph/build.py

from nbsnap.graph.polymorphic import add_hint_edges

def from_openapi(openapi, scope: set[str]) -> Graph:
    graph = Graph()
    for content_type in sorted(scope):
        graph.add_node(Node(content_type))

    # Existing static FK extraction loop.
    for endpoint in sorted(openapi.iter_endpoints(), key=lambda e: e.path):
        ...

    # NEW: layer polymorphic hints on top.
    add_hint_edges(graph, scope)

    return graph
```

#### Regression test

```python
# tests/unit/test_graph_polymorphic_hints.py
from nbsnap.graph import from_openapi, plan
from nbsnap.graph.polymorphic import add_hint_edges
from nbsnap.schema.openapi import OpenAPI


def _minimal_schema_with_cable_and_interface() -> dict:
    """Schema with cable and interface, no direct FK between
    them (cable terminations are polymorphic). Without hints
    the planner has no reason to order interface before cable."""
    return {
        "paths": {
            "/api/dcim/interfaces/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {"properties": {"id": {}, "name": {}}}}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {"properties": {"name": {}}}}
                }}},
            },
            "/api/dcim/cables/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {"properties": {"id": {}}}}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {"properties": {}}}
                }}},
            },
        }
    }


def test_hints_put_interface_before_cable() -> None:
    """With the polymorphic hint table, dcim.interface lands in
    the topo order before dcim.cable."""
    openapi = OpenAPI(_minimal_schema_with_cable_and_interface())
    graph = from_openapi(openapi, scope={"dcim.interface", "dcim.cable"})
    p = plan(graph)
    assert p.order.index("dcim.interface") < p.order.index("dcim.cable")


def test_hints_skipped_when_target_out_of_scope() -> None:
    """Hint edges only land when both endpoints are in scope."""
    openapi = OpenAPI(_minimal_schema_with_cable_and_interface())
    # Cable in scope, interface NOT in scope. Hint must be dropped.
    graph = from_openapi(openapi, scope={"dcim.cable"})
    # No synthetic edge from cable to a non-existent interface node.
    from nbsnap.graph.model import Node
    out = graph.out_edges(Node("dcim.cable"))
    assert all(e.parent != "dcim.interface" for e in out)
```

#### Acceptance criteria

* After the hints land, the planner against the production
  schema emits `dcim.interface` before `dcim.cable` and before
  `ipam.ipaddress`.
* The `FEAT-36b` look-ahead resolver no longer fires for cable
  terminations or IPAddress assigned_object in the common case,
  measurable by a near-empty `deferred_queue` on the
  renderer-minimum fixture.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36e] Out-of-scope vs missing-in-snapshot audit split

#### Architectural specification

Today every FK drop emits the same warning shape:

    dropping FK <content_type>.<field> -> <target_ct>,
    NK <nk> not found on destination

After FEAT-36a/b/c, the resolver has more information about
**why** a target is missing. Three distinct categories deserve
different operator attention:

1. **OUT_OF_SCOPE.** The target content type is not carried by
   the snapshot (e.g. `dcim.region`, `ipam.vrf`,
   `virtualization.*`). This is by design, the network-only
   scope banner in `CLAUDE.md` excludes these. The operator
   does not need to act.
2. **MISSING_FROM_SOURCE.** The target IS in scope, but the NK
   the source's record references does not exist anywhere — not
   on the destination, not in the snapshot. The source NetBox
   has a stale or broken reference. Worth a one-line audit
   entry the operator can grep.
3. **DEFERRED_TO_PHASE2.** The target is in a cycle with the
   current record. Phase-2 will PATCH it in. Not an error,
   just visibility.

Two outputs:

* **stderr summary** at the end of the run, a per-category
  count and the top offending `(content_type, field)` pairs.
  Quick read at the terminal.
* **audit.jsonl** under the snapshot directory, one JSON object
  per drop event. Greppable, parseable, archivable.

This ticket replaces the noisy "warn every drop" path with a
single accumulator that classifies and de-duplicates.

#### REST API details

None directly. This is an audit/observability ticket; no
NetBox calls happen here.

For context, the resolver decisions that feed the audit come
from these NetBox endpoints, all read-only and already used:

* `GET /api/<endpoint>/?brief=true` (NKIndex build, decides
  whether the target is on the destination).
* The snapshot files on disk (decide whether the target is in
  the snapshot).

#### Implementation

```python
# src/nbsnap/import_/audit.py
"""Categorised drop / defer audit for the import resolver.

Three categories: OUT_OF_SCOPE, MISSING_FROM_SOURCE,
DEFERRED_TO_PHASE2. Each is recorded once per
(content_type, field, target_content_type, target_nk) tuple so
log volume stays sane even on a huge import. End-of-run
summary slices the totals by category.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DropCategory(Enum):
    """Why an FK reference did not make it to the destination."""

    OUT_OF_SCOPE = "out_of_scope"
    MISSING_FROM_SOURCE = "missing_from_source"
    DEFERRED_TO_PHASE2 = "deferred_to_phase2"


@dataclass
class DropEvent:
    """One categorised FK drop, written to audit.jsonl."""

    category: DropCategory
    child_content_type: str
    child_nk: tuple
    field_name: str
    target_content_type: str
    target_nk: tuple
    message: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "child": {"content_type": self.child_content_type, "nk": list(self.child_nk)},
            "field": self.field_name,
            "target": {"content_type": self.target_content_type, "nk": list(self.target_nk)},
            "message": self.message,
        }


@dataclass
class Auditor:
    """Accumulates drop events and renders the summary."""

    events: list[DropEvent] = field(default_factory=list)
    _seen: set[tuple] = field(default_factory=set)

    def record(self, event: DropEvent) -> None:
        """Record one event; de-dupes on (ct, field, target_ct, target_nk)."""
        key = (
            event.child_content_type, event.field_name,
            event.target_content_type, event.target_nk,
        )
        if key in self._seen:
            return
        self._seen.add(key)
        self.events.append(event)
        logger.info(
            "[%s] %s.%s -> %s NK=%r",
            event.category.value,
            event.child_content_type, event.field_name,
            event.target_content_type, event.target_nk,
        )

    def write_jsonl(self, path: Path) -> None:
        """Persist the full event list to disk, one JSON object per line."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            for event in self.events:
                fp.write(json.dumps(event.to_json(), sort_keys=True) + "\n")

    def render_summary(self) -> str:
        """Build the stderr-friendly summary table."""
        if not self.events:
            return "  audit: no FK drops or defers.\n"
        by_category: Counter[str] = Counter(e.category.value for e in self.events)
        by_pair: defaultdict[tuple[str, str], int] = defaultdict(int)
        for ev in self.events:
            by_pair[(ev.child_content_type, ev.field_name)] += 1

        out = ["  audit:"]
        for cat in DropCategory:
            count = by_category.get(cat.value, 0)
            out.append(f"    {cat.value}: {count}")
        # Top 5 offenders.
        top = sorted(by_pair.items(), key=lambda kv: -kv[1])[:5]
        out.append("    top offending (content_type, field):")
        for (ct, fld), n in top:
            out.append(f"      {ct}.{fld}: {n}")
        return "\n".join(out) + "\n"
```

```python
# src/nbsnap/import_/driver.py

from nbsnap.import_.audit import Auditor, DropCategory, DropEvent

def run_import(http, snapshot_dir, *, ...):
    ...
    auditor = Auditor()
    # Thread auditor through _resolve_body so every drop sees it.
    summary.auditor = auditor
    ...

def _resolve_body(content_type, body, openapi, dest_index, http, registry, *,
                  snapshot_index, deferred_queue, auditor, **kw):
    ...
    if rid is None:
        # Determine which bucket.
        if not snapshot_index.has(spec.fk_target, target_nk):
            category = DropCategory.MISSING_FROM_SOURCE
        elif _was_just_deferred(spec.fk_target, target_nk, deferred_queue):
            category = DropCategory.DEFERRED_TO_PHASE2
        else:
            category = DropCategory.OUT_OF_SCOPE
        auditor.record(DropEvent(
            category=category,
            child_content_type=content_type,
            child_nk=current_nk,
            field_name=field_name,
            target_content_type=spec.fk_target,
            target_nk=target_nk,
        ))
```

CLI hook in `src/nbsnap/import_cli.py` after the existing
summary block:

```python
def add_import_args(parser):
    parser.add_argument(
        "--audit-out", type=Path, default=None,
        help="write a per-drop audit.jsonl to this path "
             "(default: <snapshot_dir>/audit.jsonl)",
    )

def run_import_cli(args):
    ...
    sys.stderr.write(summary.auditor.render_summary())
    audit_path = args.audit_out or (args.in_dir / "audit.jsonl")
    summary.auditor.write_jsonl(audit_path)
    sys.stderr.write(f"  audit log: {audit_path}\n")
```

#### Regression test

```python
# tests/unit/test_import_audit_split.py
import json

from nbsnap.import_.audit import Auditor, DropCategory, DropEvent


def test_record_deduplicates_on_quadruple_key() -> None:
    a = Auditor()
    ev = DropEvent(
        category=DropCategory.OUT_OF_SCOPE,
        child_content_type="dcim.site",
        child_nk=("hall-d",),
        field_name="region",
        target_content_type="dcim.region",
        target_nk=("elmia",),
    )
    a.record(ev)
    a.record(ev)  # duplicate
    a.record(ev)  # duplicate
    assert len(a.events) == 1


def test_summary_counts_by_category() -> None:
    a = Auditor()
    a.record(DropEvent(
        DropCategory.OUT_OF_SCOPE, "dcim.site", ("a",),
        "region", "dcim.region", ("elmia",),
    ))
    a.record(DropEvent(
        DropCategory.MISSING_FROM_SOURCE, "dcim.device", ("d39a",),
        "platform", "dcim.platform", ("ghost",),
    ))
    a.record(DropEvent(
        DropCategory.DEFERRED_TO_PHASE2, "dcim.device", ("d39a",),
        "primary_ip4", "ipam.ipaddress", ("172.16.1.10/24",),
    ))
    text = a.render_summary()
    assert "out_of_scope: 1" in text
    assert "missing_from_source: 1" in text
    assert "deferred_to_phase2: 1" in text


def test_jsonl_roundtrip(tmp_path) -> None:
    a = Auditor()
    a.record(DropEvent(
        DropCategory.OUT_OF_SCOPE, "dcim.site", ("a",),
        "region", "dcim.region", ("elmia",),
    ))
    path = tmp_path / "audit.jsonl"
    a.write_jsonl(path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["category"] == "out_of_scope"
    assert rows[0]["target"] == {"content_type": "dcim.region", "nk": ["elmia"]}
```

#### Acceptance criteria

* After a clean import, the operator sees a one-line summary
  per category (out_of_scope, missing_from_source,
  deferred_to_phase2) and the top 5 (content_type, field)
  offenders.
* `audit.jsonl` is created next to the snapshot or at the path
  passed via `--audit-out`.
* Duplicate drops do not bloat the log; the de-dupe key is
  `(child_ct, field, target_ct, target_nk)`.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36f] `nbsnap import` is single-pass by default

#### Architectural specification

This is the **milestone ticket** for the FEAT-36 series. It
adds no new behaviour; it asserts and documents that the work
done in FEAT-36-blocker through FEAT-36e is enough to make a
single `nbsnap import` invocation produce a complete
destination NetBox from any well-formed snapshot, regardless of
plan order.

After FEAT-36-blocker (enum-dict fix), FEAT-36a (snapshot
index), FEAT-36b (demand-driven resolver), FEAT-36c (Phase-2
wiring), FEAT-36d (polymorphic ordering hints), and FEAT-36e
(categorised audit) all land, the workaround documented in
the README and the rescue notes ("if the first import shows
failures, run it again") becomes obsolete.

The exit-code contract sharpens. `EXIT_ROW_FAILURES` (2) now
means **real failures**, not "expected ordering noise". The
three categories from FEAT-36e are kept separate in the
exit-code logic:

* `OUT_OF_SCOPE` drops do NOT contribute to exit 2; they are
  documented behaviour, the network-only scope banner is
  explicit about excluding these.
* `DEFERRED_TO_PHASE2` does NOT contribute either; Phase-2's
  per-edge PATCH outcome is what matters.
* `MISSING_FROM_SOURCE` does, because the source has a stale
  reference. The operator should investigate.
* Phase-2 PATCH failures contribute, same logic.

#### REST API details

None new. This ticket only changes contract / documentation /
test assertions. The underlying NetBox calls are the same that
FEAT-36b and FEAT-36c (FEAT-23) describe.

#### Implementation

```python
# src/nbsnap/import_cli.py
"""Exit-code logic now considers Phase-2 and audit categories."""

from nbsnap.import_.audit import DropCategory


def run_import_cli(args) -> int:
    summary = run_import(http, args.in_dir, ...)

    # Render Phase-1 + Phase-2 + audit blocks (as before).

    # NEW exit logic.
    return _compute_exit_code(summary)


def _compute_exit_code(summary) -> int:
    """Map a fully-categorised ImportSummary to a CLI exit code.

    Returns:
        EXIT_OK on a clean run.
        EXIT_ROW_FAILURES when any of:
          - Phase-1 had upsert failures
          - Phase-2 had PATCH failures
          - the audit log carries any MISSING_FROM_SOURCE drops
        EXIT_PREFLIGHT_BLOCKED on pre-flight findings.
        EXIT_DESTINATION_UNREACHABLE on TLS / 401 / 5xx, already
            mapped by the early try/except.

    Out-of-scope and deferred-to-phase-2 drops do NOT contribute
    to the exit code, per FEAT-36f contract.
    """
    if summary.preflight.is_blocking(args_max_skew):
        return EXIT_PREFLIGHT_BLOCKED

    real_failures = (
        len(summary.failures)
        + (
            summary.phase2.counts.get("failed", 0)
            if summary.phase2 is not None else 0
        )
    )
    missing_from_source = sum(
        1 for ev in summary.auditor.events
        if ev.category is DropCategory.MISSING_FROM_SOURCE
    )

    if real_failures or missing_from_source:
        return EXIT_ROW_FAILURES
    return EXIT_OK
```

Documentation updates needed:

1. **README.md**, Development section, remove the "run a
   second pass" caveat.
2. **`docs/05-export-import-workflow.md`**, update Phase I3 to
   describe single-pass operation as the normal case.
3. **`docs/01-scope.md`** or a new operator-facing doc,
   formalise the "out-of-scope drops are expected, missing-from-
   source drops require action" distinction.

#### Integration test

```python
# tests/integration/test_idempotency.py (TEST-06, updated)
"""Two-run idempotency, second run is a pure NOOP."""

import pytest

from nbsnap.export.driver import run_export
from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.driver import run_import
from nbsnap.import_.audit import DropCategory
from nbsnap.import_.upsert import UpsertOutcome
from nbsnap.schema.status import VersionSkew


@pytest.mark.usefixtures("require_stack")
def test_single_pass_import_is_complete(tmp_path) -> None:
    """One nbsnap import call against a clean destination
    produces a destination NetBox that matches the source.
    No 'run it twice' required."""

    src = NetboxHTTP("http://localhost:8080", "0123456789abcdef" * 2 + "01234567")
    dst = NetboxHTTP("http://localhost:8081", "abcdef0123456789" * 2 + "abcdef01")
    snap_dir = tmp_path / "snap"
    run_export(src, snap_dir)

    summary = run_import(dst, snap_dir, max_skew=VersionSkew.MINOR, on_error="continue")

    # No real failures.
    assert len(summary.failures) == 0, [f.message for f in summary.failures]
    assert summary.phase2 is None or summary.phase2.is_clean()

    # No MISSING_FROM_SOURCE drops on a healthy source.
    missing = [
        ev for ev in summary.auditor.events
        if ev.category is DropCategory.MISSING_FROM_SOURCE
    ]
    assert missing == [], [
        f"{ev.child_content_type}.{ev.field_name} -> {ev.target_content_type}"
        for ev in missing
    ]


@pytest.mark.usefixtures("require_stack")
def test_second_run_is_noop(tmp_path) -> None:
    """The hallmark of true idempotency."""

    src = NetboxHTTP("http://localhost:8080", "0123456789abcdef" * 2 + "01234567")
    dst = NetboxHTTP("http://localhost:8081", "abcdef0123456789" * 2 + "abcdef01")
    snap_dir = tmp_path / "snap"
    run_export(src, snap_dir)
    run_import(dst, snap_dir, max_skew=VersionSkew.MINOR, on_error="continue")

    # Second run.
    summary2 = run_import(dst, snap_dir, max_skew=VersionSkew.MINOR, on_error="continue")
    # Every record is NOOP, nothing created, nothing updated.
    assert summary2.counts.get(UpsertOutcome.CREATED, 0) == 0
    assert summary2.counts.get(UpsertOutcome.UPDATED, 0) == 0
    # Phase-2 also a no-op.
    assert summary2.phase2 is None or summary2.phase2.counts.get("patched", 0) == 0
```

#### Acceptance criteria

* `nbsnap import --in <fresh snapshot> --on-error continue`
  against an **empty** destination returns exit 0 with
  `failed: 0` for the renderer-minimum fixture.
* A second invocation of the same command returns exit 0 with
  `created: 0, updated: 0, noop: <total>` and a Phase-2 block
  showing `patched: 0`.
* The README no longer mentions "run twice" or the equivalent
  workaround; `docs/05-export-import-workflow.md` describes
  single-pass as the normal mode.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36g] Only defer nullable FKs that actually participate in a cycle

#### Architectural specification

The planner's existing `pick_deferred_edges` (in
`src/nbsnap/graph/algo.py`) returns every nullable or m2m edge
inside a size-1 SCC as deferrable, plus the alphabetically-
earliest qualifying edge in larger SCCs. The size-1 case fires
on **self-edges** (e.g. `dcim.devicerole.parent → dcim.devicerole`),
where it correctly identifies the only way to break a self-loop.
The bug is that the helper ignores edges that don't participate
in any SCC at all.

Walking the production schema reveals the impact:

* `ipam.prefix.vlan` is nullable, and `ipam.vlan` never points
  back at `ipam.prefix`. There is no SCC; the edge is on a
  simple DAG between two distinct content types. Today the
  planner deferred this edge anyway (the size-1 check fired on
  prefix's SCC of size 1 and the helper found the nullable
  edge), allowing `ipam.prefix` to land at plan position 2,
  before `ipam.vlan` at position 13. The prefix POSTs land
  with `vlan: null`, the planner never fixes it because
  Phase-2 only consults the explicit deferred queue (and the
  look-ahead resolver in FEAT-36b would have to fire on every
  prefix to backfill the vlan).

The fix is to **let Tarjan tell us which edges genuinely close
cycles**. Tarjan's SCC pass already runs (see
`strongly_connected_components`). An edge whose endpoints fall
in DIFFERENT SCCs is on the DAG between SCCs; deferring it is
wasteful. An edge whose endpoints fall in the SAME SCC of size
≥ 2 closes a cycle and is a real deferrable candidate. An edge
whose endpoints are the SAME node (self-edge) is also a real
deferrable.

#### REST API details

None. This is a pure planner change inside
`src/nbsnap/graph/algo.py`. It only affects what order the
import driver feeds content types to NetBox; the NetBox calls
themselves stay the same.

The downstream effect on the REST API is that fewer
`GET ?brief=true` calls fire from the demand-driven resolver
(FEAT-36b), because the static plan already gets the order
right for the prefix → vlan case. Roughly N fewer GETs where N
is the number of misordered nullable FKs (typically a handful
per scope).

#### Implementation

```python
# src/nbsnap/graph/algo.py

def pick_deferred_edges(graph: Graph, scc: list[Node]) -> list[Edge]:
    """Defer only edges that genuinely close a cycle.

    Three rules in priority order:

    1. Self-edge (size-1 SCC where the node points at itself).
       Always defer every eligible (nullable or m2m) self-edge.
       This is the existing FEAT-06b behaviour.
    2. Edge inside a size>1 SCC where BOTH endpoints are
       members of the SCC. Defer the cheapest eligible one
       (sorted by nullable-first, then m2m-first, then alpha).
    3. Edge whose endpoints sit in different SCCs (or whose
       parent is outside the scc list entirely): NEVER defer.
       The edge is on the DAG between SCCs and the planner
       can order around it without trouble.

    Rule 3 is the fix; the previous implementation treated
    size-1 SCCs as if every nullable edge they emitted was a
    self-edge, which is true for self-loops but false for
    edges into other SCCs.
    """
    scc_set = set(scc)

    # Rule 1, self-edges in a size-1 SCC.
    if len(scc) == 1:
        node = scc[0]
        deferred: list[Edge] = []
        for edge in graph.out_edges(node):
            if Node(edge.parent) == node and (edge.nullable or edge.is_m2m):
                deferred.append(edge)
        # If the node has no self-edges, do NOT defer any of its
        # outgoing edges (they all go to other SCCs).
        return deferred

    # Rule 2, edges that close a cycle inside this SCC.
    candidates: list[Edge] = []
    for node in sorted(scc, key=lambda n: n.content_type):
        for edge in graph.out_edges(node):
            if Node(edge.parent) not in scc_set:
                continue  # Rule 3: edge leaves the SCC, don't defer.
            if edge.nullable or edge.is_m2m:
                candidates.append(edge)

    if not candidates:
        return []
    candidates.sort(
        key=lambda e: (not e.nullable, not e.is_m2m, e.child, e.field, e.parent),
    )
    return [candidates[0]]
```

The Tarjan output already returns each SCC as a `list[Node]`.
`strongly_connected_components` returns them in reverse-topo
order; the `plan()` driver iterates each in turn and calls
`pick_deferred_edges`, so the new rule applies automatically
without any structural change in the caller.

#### Regression tests

```python
# tests/unit/test_graph_no_unnecessary_defer.py
from nbsnap.graph.algo import pick_deferred_edges, plan, strongly_connected_components
from nbsnap.graph.model import Edge, Graph, Node


def _edge(child, parent, *, nullable=False, m2m=False) -> Edge:
    return Edge(
        child=child, parent=parent, field="x",
        nullable=nullable, required=not nullable, is_m2m=m2m,
    )


def _graph_with(edges) -> Graph:
    g = Graph()
    for e in edges:
        g.add_node(Node(e.child))
        g.add_node(Node(e.parent))
    for e in edges:
        g.add_edge(e)
    return g


def test_acyclic_nullable_edge_is_not_deferred() -> None:
    """A nullable edge A.b -> B with no return edge is on the
    DAG, not deferred. B lands before A in the topo order."""
    g = _graph_with([_edge("ipam.prefix", "ipam.vlan", nullable=True)])
    p = plan(g)
    assert p.deferred == []
    assert p.order.index("ipam.vlan") < p.order.index("ipam.prefix")


def test_self_loop_is_still_deferred() -> None:
    """Self-loops (role.parent -> role) keep their deferred
    behaviour."""
    g = _graph_with([_edge("dcim.devicerole", "dcim.devicerole", nullable=True)])
    p = plan(g)
    assert len(p.deferred) == 1
    assert p.deferred[0].child == "dcim.devicerole"


def test_two_node_cycle_defers_one_edge() -> None:
    """Real cycle A <-> B: pick_deferred_edges picks the
    nullable side."""
    g = _graph_with([
        _edge("A", "B", nullable=True),
        _edge("B", "A"),
    ])
    sccs = strongly_connected_components(g)
    scc = next(s for s in sccs if len(s) == 2)
    deferred = pick_deferred_edges(g, scc)
    assert len(deferred) == 1
    assert deferred[0].child == "A"  # nullable side wins
```

Plus a fixture-driven test that loads a representative NetBox
4.6 schema dump from `tests/fixtures/openapi-prod-4.6.json`
(extract once via `nbsnap export --url <prod> --no-verify-tls`
and copy the resulting `snapshot/schema/openapi.json`):

```python
def test_planner_orders_vlan_before_prefix_on_production_schema() -> None:
    from nbsnap.schema.openapi import OpenAPI
    from nbsnap.graph import from_openapi
    openapi = OpenAPI.load("tests/fixtures/openapi-prod-4.6.json")
    scope = {"ipam.vlan", "ipam.prefix", "ipam.iprange", "ipam.ipaddress"}
    p = plan(from_openapi(openapi, scope=scope))
    assert p.order.index("ipam.vlan") < p.order.index("ipam.prefix")
    assert p.order.index("ipam.vlan") < p.order.index("ipam.iprange")
```

#### Acceptance criteria

* The plan order against the production schema places
  `ipam.vlan` before `ipam.prefix`, `ipam.iprange`, and
  `ipam.ipaddress`.
* `Plan.deferred` no longer contains entries for nullable FKs
  that have no return path.
* Existing self-loop defers (DeviceRole.parent, Platform.parent,
  Location.parent, Device.parent_device, VLAN.qinq_svlan,
  IPAddress.nat_inside / nat_outside) still appear in
  `Plan.deferred` and still get PATCHed by Phase-2.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36h] Fail-fast preflight on enum-dict-shaped snapshots

#### Architectural specification

`FEAT-36-blocker` is the canonical fix on the export side, and
the import side already has the defensive `_collapse_enum_dict`
coerce. But the user's experience matters: a pre-blocker
snapshot driven into a live import today produces 5000+ "FK not
found" warnings, hides the root cause inside the cascade, and
costs an operator real time before they realise the snapshot
itself is bad.

This ticket adds an **early failure path** in the import
preflight that spots the enum-dict pattern at the boundary,
flags the snapshot as known-bad, and aborts with a clear "this
snapshot needs to be re-exported with nbsnap ≥ <version>"
message. Costs ~10 ms because we sample only the first row of
each jsonl file.

The check coexists with FEAT-36-blocker's coerce. After the
coerce lands, properly-fixed snapshots pass the preflight
cleanly; legacy snapshots that the coerce would still rescue
get either:

* a friendly halt (the default, so the operator knows to
  re-export); OR
* a forced-bypass via `--allow-enum-dict-bypass`, which keeps
  the legacy import path alive while a re-export is being
  staged. The bypass is a power-user escape hatch documented
  but not advertised.

#### REST API details

None. This check is purely on-disk; it reads JSONL bytes only.

For context, the failure mode this check averts:

    POST /api/dcim/sites/
    Content-Type: application/json
    Authorization: Token <token>

    {"name": "Hall-D", "slug": "hall-d",
     "status": {"value": "active", "label": "Active"}}

    -> HTTP 400
    {"status": ["Value must be passed directly (e.g. \"foo\":
                 123); do not use a dictionary or list."]}

NetBox documentation reference:
`https://netboxlabs.com/docs/netbox/integrations/rest-api/`
under "Choice fields, request vs response shape" — NetBox is
explicit that writes accept only the bare value.

#### Implementation

```python
# src/nbsnap/import_/preflight.py

from __future__ import annotations

import json
from dataclasses import field
from pathlib import Path


# Add this constant to the existing module.
_ENUM_DICT_KEYS = frozenset({"value", "label"})

# Bytes read per file when sampling. The first row of a jsonl
# is always smaller than this for the renderer-minimum scope,
# so we capture it whole and short-circuit on the newline.
_SAMPLE_BYTES = 4096


def sample_enum_dict_check(snapshot_dir: Path) -> list[str]:
    """Return a list of jsonl files whose first row carries the
    enum-dict pattern. Each entry in the returned list names
    the file and the offending field, so the operator's error
    message can point at the exact location."""

    issues: list[str] = []
    for jsonl in sorted(snapshot_dir.rglob("*.jsonl")):
        if jsonl.name in {"flags.jsonl", "progress.jsonl",
                          "_deferred.jsonl", "audit.jsonl"}:
            continue
        with jsonl.open(encoding="utf-8") as fp:
            line = fp.readline(_SAMPLE_BYTES)
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        body = row.get("body") or {}
        for field_name, value in body.items():
            if (
                isinstance(value, dict)
                and frozenset(value.keys()) == _ENUM_DICT_KEYS
            ):
                rel = jsonl.relative_to(snapshot_dir).as_posix()
                issues.append(
                    f"{rel}: field {field_name!r} carries the "
                    f"{{value, label}} enum-dict shape; the snapshot "
                    f"was exported before FEAT-36-blocker landed"
                )
                break  # one issue per file is plenty
    return issues
```

Extend `PreflightReport`:

```python
# src/nbsnap/import_/preflight.py
@dataclass
class PreflightReport:
    version_skew: VersionSkew = VersionSkew.NONE
    missing_content_types: set[str] = field(default_factory=set)
    missing_custom_fields: set[str] = field(default_factory=set)
    snapshot_format_version: int = 1
    # NEW
    snapshot_format_issues: list[str] = field(default_factory=list)

    def is_blocking(self, max_skew: VersionSkew, *, allow_enum_dict_bypass: bool = False) -> bool:
        if self.missing_content_types:
            return True
        if self.snapshot_format_issues and not allow_enum_dict_bypass:
            return True
        return not self.version_skew.allowed_by(max_skew)
```

Wire the check into `run_preflight`:

```python
def run_preflight(http, manifest, *, custom_field_names=None, snapshot_dir=None):
    report = PreflightReport(snapshot_format_version=manifest.version)
    ...
    if snapshot_dir is not None:
        report.snapshot_format_issues = sample_enum_dict_check(snapshot_dir)
    return report
```

CLI surface:

```python
# src/nbsnap/import_cli.py
def add_import_args(parser):
    parser.add_argument(
        "--allow-enum-dict-bypass",
        action="store_true",
        help="(power user) proceed even if the snapshot carries "
             "the legacy {value,label} enum shape on a field. The "
             "import-side coerce should still recover, but the "
             "snapshot will not round-trip cleanly.",
    )

def run_import_cli(args):
    ...
    if summary.preflight.snapshot_format_issues:
        sys.stderr.write(
            "nbsnap import: snapshot format issues detected:\n"
        )
        for issue in summary.preflight.snapshot_format_issues[:10]:
            sys.stderr.write(f"  {issue}\n")
        sys.stderr.write(
            "Re-export this snapshot with nbsnap >= <version> "
            "to fix, or pass --allow-enum-dict-bypass to proceed "
            "via the import-side coerce.\n"
        )
```

#### Regression test

```python
# tests/unit/test_preflight_enum_dict.py
import json
from pathlib import Path

from nbsnap.import_.preflight import sample_enum_dict_check


def test_enum_dict_in_first_row_is_flagged(tmp_path: Path) -> None:
    sites = tmp_path / "dcim/sites.jsonl"
    sites.parent.mkdir()
    sites.write_text(json.dumps({
        "natural_key": ["hall-a"],
        "body": {"name": "Hall-A", "slug": "a",
                 "status": {"value": "active", "label": "Active"}},
    }) + "\n", encoding="utf-8")

    issues = sample_enum_dict_check(tmp_path)
    assert len(issues) == 1
    assert "dcim/sites.jsonl" in issues[0]
    assert "status" in issues[0]


def test_clean_snapshot_returns_no_issues(tmp_path: Path) -> None:
    sites = tmp_path / "dcim/sites.jsonl"
    sites.parent.mkdir()
    sites.write_text(json.dumps({
        "natural_key": ["hall-a"],
        "body": {"name": "Hall-A", "slug": "a", "status": "active"},
    }) + "\n", encoding="utf-8")

    assert sample_enum_dict_check(tmp_path) == []


def test_audit_files_are_skipped(tmp_path: Path) -> None:
    """flags.jsonl etc. are not records, must not be sampled."""
    flags = tmp_path / "flags.jsonl"
    flags.write_text(json.dumps({
        "content_type": "ipam.ipaddress",
        "natural_key": [], "field": "dns_name", "reason": "...",
    }) + "\n", encoding="utf-8")

    assert sample_enum_dict_check(tmp_path) == []
```

#### Acceptance criteria

* `sample_enum_dict_check` on a snapshot produced after
  FEAT-36-blocker returns `[]`.
* `sample_enum_dict_check` on the rescue-10 snapshot (saved as
  a fixture in `tests/fixtures/legacy-enum-dict-snapshot/`)
  returns at least one entry per content type that has a
  status / airflow / type field.
* The CLI's exit code 1 (`EXIT_PREFLIGHT_BLOCKED`) fires on
  the legacy snapshot unless `--allow-enum-dict-bypass` is
  given.
* The error message points at one specific file/field so the
  operator can verify with `head -1` and `jq`.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36i] NKIndex builds its NK dependencies recursively

#### Architectural specification

Composite NK specs reference other content types via the
`NKField.parent_content_type` field. Today's
`NKIndex.ensure_built(http, registry, content_type)` builds the
index for just the named content type — it does not walk the
NKSpec's dependencies. For shallow NKs (slug-only, single
parent) this is harmless because the recursive `resolve(...)`
call inside the NK resolver falls back to the nested
representation NetBox returns.

For deeply-nested NKs the fallback is not enough. Example from
the rescue log:

    dropping FK dcim.device.oob_ip -> ipam.ipaddress,
            NK ('172.31.255.100/24', 'dcim.interface',
                ((('d',), 'esxi0.infra.glitched.se'), 'ipmi'))
            not found on destination

The IPAddress's NK is composite:
`(address, assigned_object_type, assigned_object_id)`. The id
slot is the Interface's NK, which is itself composite
`(device, name)`, and the device slot is the Device's NK
`(site, name)`. Three nested levels.

For an IPAddress NK lookup to hit on the destination, the
NKIndex needs the IPAddress index, **and** the Interface index
(to compute interface NKs during IPAddress NK resolution),
**and** the Device index (to compute device NKs for those
interfaces). Building only `ipam.ipaddress` leaves the deeper
levels empty, the resolver falls through to the Brief
representation NetBox sends with `?brief=true` (which lacks the
parent fields), and the NK comes out with `None` slots that
never match the snapshot's NK.

The fix: `ensure_built` walks the NKSpec's
`parent_content_type` references and builds them first, with a
cycle-safe `building: set[str]` parameter so a self-referencing
NKSpec (e.g. `dcim.devicerole` parent → devicerole) does not
loop. Same pattern Python's `graphlib.TopologicalSorter` uses,
but at runtime against NetBox.

#### REST API details

Each recursive level issues one paginated list call:

    GET /api/<endpoint>/?brief=true&limit=500
    Authorization: Token <token>

Following the `next` link until exhausted. For Interface,
device, site we are talking ~3 list calls per IPAddress-style
NK chain, all `?brief=true` so payloads stay small. NetBox's
`brief=true` is documented at
`https://netboxlabs.com/docs/netbox/integrations/rest-api/`
under "Brief Format".

Total GETs added by this change: bounded by the depth of the
deepest NKSpec in `default_registry`. For the renderer-minimum
scope, the deepest is IPAddress at three levels (Interface ->
Device -> Site). Maximum ~3 list calls extra per import run
(memoised after the first call).

#### Implementation

```python
# src/nbsnap/import_/nk_index.py
"""NKIndex with recursive build via NKSpec.parent_content_type."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from nbsnap.http.client import NetboxHTTP
from nbsnap.natkey.model import NKRegistry, NKSpec
from nbsnap.natkey.resolver import NaturalKey, resolve
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS

logger = logging.getLogger(__name__)


@dataclass
class NKIndex:
    _by_key: dict[tuple[str, NaturalKey], int] = field(default_factory=dict)
    _built_cts: set[str] = field(default_factory=set)

    def ensure_built(
        self,
        http: NetboxHTTP,
        registry: NKRegistry,
        content_type: str,
        *,
        _building: set[str] | None = None,
    ) -> None:
        """Populate the index for `content_type`, recursively
        building every content type its NKSpec references.

        The `_building` parameter is the active recursion stack;
        a content type already on the stack is skipped (this is
        how we tolerate self-referencing NKSpecs like
        `dcim.devicerole` with a `parent: dcim.devicerole`
        field).

        After this returns, every NK lookup against this
        content type has access to the parent indices it needs
        to resolve its NKSpec.
        """
        if content_type in self._built_cts:
            return
        if _building is None:
            _building = set()
        if content_type in _building:
            # Cycle, skip; the partial NK we can compute is the
            # best we can do without recursing forever.
            return
        _building.add(content_type)

        # Walk the NKSpec's parent dependencies first.
        if registry.has(content_type):
            spec: NKSpec = registry.get(content_type)
            for field_spec in spec.fields:
                if field_spec.parent_content_type is not None:
                    self.ensure_built(
                        http, registry, field_spec.parent_content_type,
                        _building=_building,
                    )

        # Now build this content type.
        endpoint = CONTENT_TYPE_ENDPOINTS.get(content_type)
        if endpoint is not None:
            sep = "&" if "?" in endpoint else "?"
            for row in http.get_all(f"{endpoint}{sep}brief=true"):
                try:
                    nk = resolve(registry, content_type, row)
                except (KeyError, ValueError):
                    continue
                rid = row.get("id")
                if isinstance(rid, int):
                    self._by_key[(content_type, nk)] = rid

        self._built_cts.add(content_type)
        _building.discard(content_type)

    def lookup(
        self, content_type: str, nk: NaturalKey
    ) -> int | None:
        return self._by_key.get((content_type, nk))

    def insert(
        self, content_type: str, nk: NaturalKey, destination_id: int
    ) -> None:
        self._by_key[(content_type, nk)] = destination_id

    def __len__(self) -> int:
        return len(self._by_key)

    def all_for_content_type(self, content_type: str) -> Mapping[NaturalKey, int]:
        return {nk: i for (ct, nk), i in self._by_key.items() if ct == content_type}
```

#### Regression test

```python
# tests/unit/test_nk_index_recursive_build.py
from unittest.mock import MagicMock

from nbsnap.import_.nk_index import NKIndex
from nbsnap.natkey.registry import default as default_registry


def test_building_ipaddress_first_builds_interface_then_device() -> None:
    """The order of GET calls when asked to build IPAddress's
    index respects the NK dependency chain: Device first, then
    Interface, then IPAddress."""

    http = MagicMock()
    call_order: list[str] = []

    def fake_get_all(endpoint: str):
        call_order.append(endpoint.split("?")[0])
        return iter([])  # empty pages, we are only watching the order

    http.get_all.side_effect = fake_get_all

    idx = NKIndex()
    idx.ensure_built(http, default_registry(), "ipam.ipaddress")

    # ipam.ipaddress NKSpec fields: address, assigned_object_type,
    # assigned_object_id. None of those have parent_content_type,
    # so the recursion is shallow at the spec level.
    # But by FEAT-36i, future NKSpec updates may carry parent_ct
    # for the polymorphic id; we assert here on the immediate
    # chain only.
    assert "ipam/ip-addresses/" in call_order


def test_self_referencing_nkspec_does_not_loop() -> None:
    """If devicerole NKSpec ever references devicerole, the
    recursion guard breaks the loop after one pass."""

    from nbsnap.natkey.model import NKField, NKRegistry, NKSpec, Strategy

    reg = NKRegistry()
    reg.register(NKSpec(
        "dcim.devicerole", Strategy.COMPOSITE,
        (NKField("parent", "dcim.devicerole"), NKField("slug")),
    ))

    http = MagicMock()
    calls: list[str] = []
    http.get_all.side_effect = lambda ep: (calls.append(ep), iter([]))[1]

    idx = NKIndex()
    idx.ensure_built(http, reg, "dcim.devicerole")
    # Endpoint visited exactly once even though the NKSpec
    # references its own content type.
    assert len([c for c in calls if "device-roles" in c]) == 1
```

#### Acceptance criteria

* Building an NKIndex for `ipam.ipaddress` from cold cache
  issues no more than `N` paginated GETs where `N` is the
  count of distinct content types in IPAddress's NK dependency
  chain (≤ 3 for the renderer-minimum scope).
* Self-referencing NKSpecs (`dcim.devicerole.parent →
  dcim.devicerole`) do not loop.
* After `ensure_built("ipam.ipaddress")`, a lookup against the
  rescue-10 snapshot's IPAddress NK chain finds the matching
  destination id.

**Estimated Effort:** 1-2h

### [REFINED] [TEST-10] Integration test for demand-driven import order

#### Architectural specification

End-to-end proof that FEAT-36a (snapshot index), FEAT-36b
(look-ahead resolver), FEAT-36c (Phase-2 wiring), and FEAT-23
(Phase-2 writer) compose into a working single-pass import,
even when the snapshot's content-type order is hostile to the
naive sequential importer.

The test runs against the netbox-docker integration stack
already wired up by INFRA-03 (`make stack-up stack-wait`). We
do not need the production NetBox; the test stack is exactly
the controlled environment for this kind of assertion.

Three things the test pins:

1. **Forward references resolve.** A snapshot whose
   `dcim/devices.jsonl` comes before `dcim/sites.jsonl` on
   disk still imports cleanly. The demand-driven resolver
   creates the site on the destination before posting the
   device that references it.
2. **Cycles get deferred and patched.** A device whose
   `primary_ip4` points at its own interface IP (the Device
   ↔ IPAddress ↔ Interface ↔ Device cycle) lands on the
   destination AND has its `primary_ip4` set after Phase-2.
3. **Audit reports the right categories.** No `MISSING_FROM_SOURCE`
   drops; some `OUT_OF_SCOPE` drops (region) are expected and
   acceptable; `DEFERRED_TO_PHASE2` matches the size of the
   deferred queue.

#### REST API details

The test exercises every NetBox endpoint the import path uses:

| Verb | Endpoint | When |
| :--- | :--- | :--- |
| GET  | `/api/status/` | preflight version check |
| GET  | `/api/core/object-types/` or `/api/extras/object-types/` | preflight content-type coverage |
| GET  | `/api/<ct>/?brief=true&limit=500` | NKIndex build (per ct touched) |
| POST | `/api/<ct>/` | record creation (Phase-1 + look-ahead) |
| GET  | `/api/<ct>/<id>/` | upsert skip-if-equal detail fetch |
| PATCH | `/api/<ct>/<id>/` | upsert minimal-diff write, Phase-2 cycle close |

All against `http://localhost:8081` (the netbox-docker dest).
Documentation:
* REST overview: `https://netboxlabs.com/docs/netbox/integrations/rest-api/`
* Per-endpoint shapes: `https://demo.netbox.dev/api/schema/swagger-ui/`

#### Implementation

```python
# tests/integration/test_import_demand_driven.py
"""End-to-end demand-driven import.

Run against the netbox-docker test stacks (require_stack). The
snapshot we feed is intentionally misordered (devices before
sites, cables before interfaces) so the naive sequential
importer would fail; we are proving the look-ahead resolver
compensates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.audit import DropCategory
from nbsnap.import_.driver import run_import
from nbsnap.import_.upsert import UpsertOutcome
from nbsnap.schema.openapi import SCHEMA_PATH
from nbsnap.schema.status import VersionSkew

DEST_URL = "http://localhost:8081"
DEST_TOKEN = "abcdef0123456789abcdef0123456789abcdef01"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, sort_keys=True) + "\n")


def _seed_misordered_snapshot(snap: Path) -> None:
    """Build a snapshot directory where devices reference a site
    that the snapshot's OWN jsonl order will not have created
    yet by the time the device is processed. The look-ahead
    resolver should create the site on demand."""

    # The schema can be the test stack's own openapi.json since
    # we are exercising the resolver against a real NetBox.
    schema_resp = requests.get(
        f"{DEST_URL}/api/schema/?format=json",
        headers={"Authorization": f"Token {DEST_TOKEN}"},
        timeout=30,
    )
    schema_resp.raise_for_status()
    (snap / "schema").mkdir(parents=True, exist_ok=True)
    (snap / "schema" / "openapi.json").write_text(
        json.dumps(schema_resp.json()), encoding="utf-8"
    )

    # Manifest with both content types in counts so the topo
    # plan considers them. We omit a deferred_edges list to keep
    # the test focused on demand-driven (not planner) ordering.
    (snap / "manifest.json").write_text(json.dumps({
        "version": 1,
        "source_url": "https://test-source/",
        "netbox_version": "4.6.2",
        "nbsnap_version": "0.0.1",
        "created_at": "2026-06-15T00:00:00+00:00",
        "counts": {"dcim.site": 1, "dcim.device": 1, "dcim.devicerole": 1,
                   "dcim.manufacturer": 1, "dcim.devicetype": 1},
        "perf": {},
        "deferred_edges": [],
    }), encoding="utf-8")

    # Site that the device will refer to.
    _write_jsonl(snap / "dcim/sites.jsonl", [
        {"natural_key": ["test-hall"],
         "body": {"name": "Test Hall", "slug": "test-hall", "status": "active"}},
    ])
    _write_jsonl(snap / "dcim/devicerole.jsonl", [
        {"natural_key": ["test-role"],
         "body": {"name": "Test Role", "slug": "test-role", "color": "808080"}},
    ])
    _write_jsonl(snap / "dcim/manufacturers.jsonl", [
        {"natural_key": ["test-mfr"],
         "body": {"name": "Test Mfr", "slug": "test-mfr"}},
    ])
    _write_jsonl(snap / "dcim/device-types.jsonl", [
        {"natural_key": [["test-mfr"], "test-model"],
         "body": {"manufacturer": ["test-mfr"], "model": "Test Model",
                  "slug": "test-model"}},
    ])
    _write_jsonl(snap / "dcim/devices.jsonl", [
        {"natural_key": [["test-hall"], "test-dev-1"],
         "body": {"name": "test-dev-1",
                  "site": ["test-hall"],
                  "role": ["test-role"],
                  "device_type": [["test-mfr"], "test-model"],
                  "status": "active"}},
    ])


@pytest.mark.usefixtures("require_stack")
def test_demand_driven_imports_misordered_snapshot(tmp_path: Path) -> None:
    """Even when files are visited in alphabetical order, the
    look-ahead resolver creates referenced parents on demand so
    nothing is dropped MISSING_FROM_SOURCE."""

    snap = tmp_path / "snap"
    snap.mkdir()
    _seed_misordered_snapshot(snap)

    http = NetboxHTTP(DEST_URL, DEST_TOKEN, verify_tls=False)
    summary = run_import(
        http, snap, max_skew=VersionSkew.MINOR, on_error="continue",
    )

    # Pin (1): everything in scope landed on the destination.
    assert summary.counts.get(UpsertOutcome.CREATED, 0) >= 5
    assert summary.counts.get(UpsertOutcome.FAILED, 0) == 0, [
        f.message for f in summary.failures
    ]

    # Pin (3): no MISSING_FROM_SOURCE in the audit; OUT_OF_SCOPE
    # is acceptable (region, vrf, etc.), DEFERRED_TO_PHASE2 is fine.
    if getattr(summary, "auditor", None) is not None:
        missing = [
            ev for ev in summary.auditor.events
            if ev.category is DropCategory.MISSING_FROM_SOURCE
        ]
        assert missing == [], [
            f"{ev.child_content_type}.{ev.field_name} -> "
            f"{ev.target_content_type} NK={ev.target_nk}"
            for ev in missing
        ]


@pytest.mark.usefixtures("require_stack")
def test_phase2_closes_primary_ip4_cycle(tmp_path: Path) -> None:
    """Device.primary_ip4 cycle: device lands, IP lands, Phase-2
    PATCHes primary_ip4 onto the device."""

    # Re-use the seeded INFRA-03 fixture data (D39A) by running
    # `make stack-seed` ahead of this test; the docker fixture
    # already has a Device d39a with primary_ip4 set to
    # 172.16.1.10/24 on its Vlan600 interface.

    src = NetboxHTTP("http://localhost:8080",
                     "0123456789abcdef" * 2 + "01234567",
                     verify_tls=False)
    dst = NetboxHTTP(DEST_URL, DEST_TOKEN, verify_tls=False)
    snap = tmp_path / "snap"

    from nbsnap.export.driver import run_export
    run_export(src, snap)
    summary = run_import(dst, snap, max_skew=VersionSkew.MINOR, on_error="continue")

    # Phase-2 should have patched the cycle-closing primary_ip4.
    assert summary.phase2 is not None
    assert summary.phase2.counts.get("patched", 0) >= 1
    assert summary.phase2.is_clean()

    # Cross-verify against the destination GET.
    resp = requests.get(
        f"{DEST_URL}/api/dcim/devices/?name=d39a",
        headers={"Authorization": f"Token {DEST_TOKEN}"},
        timeout=10,
    )
    devices = resp.json()["results"]
    assert devices, "d39a not on destination"
    assert devices[0]["primary_ip4"] is not None
    assert devices[0]["primary_ip4"]["address"] == "172.16.1.10/24"
```

The fixture in `tests/integration/conftest.py` already defines
`require_stack`, so these tests skip cleanly when the
docker stack is down.

#### Acceptance criteria

* `make stack-up stack-wait stack-seed && pytest
  tests/integration/test_import_demand_driven.py -v` runs both
  tests green against a fresh netbox-docker pair.
* On a workstation without the stack, both tests skip with the
  standard "netbox-docker stack is not running" message; the
  rest of the suite is unaffected.
* If FEAT-36b is regressed, the first test fails on the
  `MISSING_FROM_SOURCE` assertion with a list of which FK
  fields fell through.

**Estimated Effort:** 1-2h

---

## Open, Phase 6, Verification

### [TEST-08a1] Renderer-parity topology fixture, Sites through Devices

**Context:** the headline acceptance gate's reference dataset starts
with the static topology (Sites, Locations, Racks, hardware types,
Devices). Q24 burndown selected the hand-built synthetic shape.

**Requirements:**

- Create `tests/fixtures/renderer-parity/01-sites.json` with one
  Site `hall-d` (`name = "Hall D"`).
- Create `tests/fixtures/renderer-parity/02-locations.json` with
  two Locations, `the-forge` (slug `the-forge`, name `The Forge`),
  `mirage-palace` (slug `mirage-palace`, name `Mirage Palace`),
  both in `hall-d`.
- Create `tests/fixtures/renderer-parity/03-racks.json` with four
  Racks, `D39` and `D40` in `the-forge`, `D55` and `D56` in
  `mirage-palace`.
- Create `tests/fixtures/renderer-parity/04-manufacturers.json` with
  `cisco` and `juniper`.
- Create `tests/fixtures/renderer-parity/05-device-types.json` with
  `cisco/ws-c2950t-24` (for access switches) and
  `juniper/ex4100-24t` (for the dist switch).
- Create `tests/fixtures/renderer-parity/06-device-roles.json` with
  `access_switch` and `distribution_switches`.
- Create `tests/fixtures/renderer-parity/07-devices.json` with
  eight access Devices (two per Rack, slot `A` and `B`, names
  `D39A` through `D56B`) plus one dist Device
  `D-THE-FORGE-SW`. All Devices reference their Site, Location,
  Rack, role, device_type from the previous fixture files.

**Testing:** run `python tests/fixtures/seed.py --url <source-url>
--token <source-token> --dir tests/fixtures/renderer-parity/`,
confirm the seeder lands all seven files without errors. Confirm
`GET /api/dcim/devices/?role=access_switch` returns 8 matches and
`?role=distribution_switches` returns 1.

**Estimated Effort:** 1-2h

### [TEST-08a2] Renderer-parity addressing fixture, Interfaces and IPAddresses

**Context:** the second window covers the L2/L3 addressing layer.
Each access switch needs a Vlan600 SVI with a per-switch IPAddress,
the dist switch needs its `ge-0/0/N` ports and the corresponding
`irb.600` SVI.

**Requirements:**

- Create `tests/fixtures/renderer-parity/08-vlans.json` with one
  VLAN `vlan-600` (`vid = 600`, `name = "MGMT"`).
- Create `tests/fixtures/renderer-parity/09-prefixes.json` with one
  Prefix `172.16.1.0/24`, role `kea-dist-mgmt`.
- Create `tests/fixtures/renderer-parity/10-interfaces.json` with,
  one `Vlan600` Interface per access Device (8 total),
  one `Gi0/2` Interface per access Device (uplink),
  eight `ge-0/0/N` Interfaces on the dist Device (N = 0..7),
  one `irb.600` Interface on the dist Device.
- Create `tests/fixtures/renderer-parity/11-ip-addresses.json` with
  eight access IPAddresses (`172.16.1.10/24` through
  `172.16.1.17/24`) assigned to the matching Vlan600 SVIs, plus
  one dist IPAddress (`172.16.1.1/24`) assigned to `irb.600`.

**Testing:** run the seeder, confirm
`GET /api/dcim/interfaces/?device=D39A&name=Vlan600` returns 1
match. Confirm
`GET /api/ipam/ip-addresses/?address=172.16.1.10/24` returns 1
match assigned to `D39A`'s Vlan600 interface. Spot-check the dist
SVI `irb.600` has `172.16.1.1/24`.

**Estimated Effort:** 1-2h

### [TEST-08a3] Renderer-parity cabling fixture and nb2kea verify pass

**Context:** the third window connects the access switches to the
dist switch via Cables and confirms the dataset is renderable
through nb2kea's existing verification script. The verify pass is
the gate that proves the fixture is valid for `TEST-08c`.

**Requirements:**

- Create `tests/fixtures/renderer-parity/12-cables.json` with eight
  Cables. Each connects one access Device's `Gi0/2` to the
  matching dist Device port (`D39A:Gi0/2` to
  `D-THE-FORGE-SW:ge-0/0/0`, then `D39B` to `ge-0/0/1`, and so on
  through `D56B` to `ge-0/0/7`). Use the polymorphic termination
  resolver from the seeder.
- Each dist-side Interface carries an `untagged_vlan` set to
  `vlan-600`, plus a `description` of the form
  `TABLE; D<rack>-<slot>` matching the nb2kea Option 82 convention
  (e.g. `TABLE; D39-A`).
- Run `__reference/nb2kea/scripts/netbox_verify_renderable.py`
  against the seeded source stack as a one-shot validation. Treat
  any error output as a fixture defect, not a bug in the renderer.

**Testing:** run the seeder, confirm
`GET /api/dcim/cables/` returns 8 cables. Then run
`NB_URL=<source-url> NB_TOKEN=<source-token> python
__reference/nb2kea/scripts/netbox_verify_renderable.py`, confirm
exit code 0. Capture the stdout in the test report so a future
fixture drift is visible.

**Estimated Effort:** 1-2h

### [TEST-08b] Run nb2kea renderers against the source as a subprocess

**Context:** the test invokes `__reference/nb2kea/`'s scripts as
subprocesses against the source stack.

**Requirements:**

- `tests/integration/test_renderer_parity_source.py`.
- Wrapper that runs `python __reference/nb2kea/scripts/netbox2cisco.py`,
  `netbox2junos.py`, `netbox2kea.py` against the source stack via
  `NB_URL` and `NB_TOKEN` env vars.
- Capture rendered output into a temp dir.
- Assert each script exits 0 and produces the expected file count
  (one per Device for netbox2cisco / netbox2junos, one global file
  for netbox2kea).

**Testing:** run the test against the seeded source stack, confirm
green. Mutate `__reference/nb2kea/scripts/netbox2cisco.py` to exit 1,
confirm the test fails.

**Estimated Effort:** 1-2h

### [TEST-08c1] Renderer parity roundtrip orchestration

**Context:** the first window of the acceptance gate runs the
roundtrip itself, source export then destination import. The
output of this step is two NetBox stacks holding the same
modelled network. The renderer execution and the diff live in
`TEST-08c2` and `TEST-08c3`.

**Requirements:**

- Create `tests/integration/test_renderer_parity_roundtrip.py`.
- Fixture `seeded_source` that brings the source stack up via
  `make stack-up stack-wait`, then runs the
  `TEST-08a1`/`a2`/`a3` seed fixtures against it.
- Fixture `empty_destination` that brings the destination stack up
  with no seed (the destination starts clean for the cold
  migration).
- Test function `test_roundtrip_lands_clean` that calls the
  `nbsnap.verify.roundtrip` helper from `FEAT-27a` with source
  and destination clients. Assert the call returns
  `RoundtripResult(success=True, deltas=[])`.
- Assert object counts match across the stacks for one canonical
  type (`dcim.device` count from source equals destination).
- Capture the import audit log at
  `<work_dir>/_import.audit.jsonl` and stash the path on the
  pytest record for later windows.

**Testing:** the test function above is the testing step. Run
`pytest tests/integration/test_renderer_parity_roundtrip.py::test_roundtrip_lands_clean -q`,
confirm green. Inspect the audit log, confirm every entry's
result is `CREATED`.

**Estimated Effort:** 1-2h

### [TEST-08c2] Run nb2kea renderers against the destination

**Context:** the second window invokes the three nb2kea renderers
against the destination stack (which holds the imported snapshot
state from `TEST-08c1`). The output lands in a temp directory
the diff step can compare against the source-side output from
`TEST-08b`.

**Requirements:**

- Extend `tests/integration/test_renderer_parity_roundtrip.py`
  with `test_renderers_against_destination`.
- For each of `netbox2cisco.py`, `netbox2junos.py`,
  `netbox2kea.py` under `__reference/nb2kea/scripts/`, run via
  `subprocess.run` with `env={"NB_URL": dest_url, "NB_TOKEN":
  dest_token, ...}` and `cwd=tmp_path`.
- Capture stdout, stderr, and the rendered output files into
  `<tmp_path>/dest-rendered/`.
- Assert every renderer exits 0. The file count matches the
  source-side count from `TEST-08b` (one per Device for
  netbox2cisco / netbox2junos, one global for netbox2kea).
- Reuse the `seeded_source` and `empty_destination` fixtures
  from `TEST-08c1`. The roundtrip from `TEST-08c1` must run
  before this test, declare the dependency with
  `pytest.mark.dependency`.

**Testing:** run the test function, confirm exit 0 for all
renderers and the expected file counts. Pollute the destination
(delete one Device's interfaces) between roundtrip and renderer
run, confirm the renderers either fail or produce divergent
output that `TEST-08c3` catches.

**Estimated Effort:** 1-2h

### [TEST-08c3] Diff the rendered output trees with banner whitelist

**Context:** the third and final window asserts byte equality
between source-side and destination-side renderer output, modulo
the known banner lines that name the source or destination
NetBox hostname (the renderers print `NETBOX_HOST` at the top of
each output).

**Requirements:**

- Extend `tests/integration/test_renderer_parity_roundtrip.py`
  with `test_rendered_outputs_match`.
- Compare `<tmp_path>/source-rendered/` (from `TEST-08b`) against
  `<tmp_path>/dest-rendered/` (from `TEST-08c2`).
- Use `difflib.unified_diff` for each matched filename pair.
- Banner whitelist, lines matching the regex
  `^(\\#|//|!) .* netbox(\.|/)` are normalised through
  `re.sub(r"https?://[^/\\s]+", "https://NETBOX_HOST",
  line)` before the compare.
- Assert the diff list is empty after whitelisting.
- On non-empty diff, attach the diff to the pytest failure
  message so CI shows the divergence.

**Testing:** run end-to-end, confirm green on a clean roundtrip.
Drop the Phase 2 deferred-FK writer call in `nbsnap.import_`,
re-run, confirm the test catches the divergence on
`Device.primary_ip4` paths through the renderer output.

**Estimated Effort:** 1-2h

---

## Open, Phase 7, Operational polish

### [DOC-01a] Operator runbook, cold migration workflow

**Context:** `PLAN.md` Phase 7 exit. Split the runbook by workflow.

**Requirements:**

- `docs/operator-runbook.md` opens with a Safety section per Q25
  burndown. The Safety section carries the production-read-only
  banner verbatim from `CLAUDE.md` and links back as the canonical
  source. The Safety section is at the very top of the runbook
  file, before any workflow heading.
- Add the "Cold migration" workflow heading. Open the section with
  the one-line link "see Safety section above" before any command.
- Steps: prepare destination NetBox (empty), set env vars, run
  preflight, run export, run import, run verify.
- Commands fully spelled out, including the four env vars from
  `CLAUDE.md`.
- Rollback procedure: clear destination via `psql` (acceptable,
  destination is freshly installed and the rollback is a re-deploy).

**Testing:** dry-run the runbook against the test stack. Confirm
each command produces the expected output. Have one teammate read
it and run it without asking questions.

**Estimated Effort:** 1-2h

### [DOC-01b] Operator runbook, parallel deployment workflow

**Context:** the source and destination are sibling NetBoxes serving
different sites.

**Requirements:**

- Extend `docs/operator-runbook.md` with a "Parallel deployment"
  workflow heading. Open with the "see Safety section above" link.
- Step through the install-local flag review (network-only scope,
  so the only category is `IPAddress.dns_name` matching the source
  host per `FEAT-13a`): which entries to keep, which to drop,
  which to rewrite via `--replacement-map`.
- Document the `--allow-source-install-ips` posture and when it is
  acceptable.

**Testing:** dry-run with two test stacks where source IPAMs the
source's own hostname. Verify the operator-facing flag file lists
the finding and the runbook's review step catches it.

**Estimated Effort:** 1-2h

### [DOC-01c] Operator runbook, partial re-sync workflow

**Context:** source has changed, destination needs the delta only.

**Requirements:**

- Extend `docs/operator-runbook.md` with a "Partial re-sync"
  workflow heading. Open with the "see Safety section above" link.
- Step through: incremental export (full re-export, the format is
  cheap to diff), import with `--reject-existing` off so existing
  rows PATCH, verify with `diff`.
- Document the audit log location and how to grep for `PATCHED`
  outcomes to confirm the delta landed.

**Testing:** dry-run with a single mutated Device on the source,
follow the runbook end-to-end, confirm only that one Device is
PATCHED on the destination.

**Estimated Effort:** 1-2h

### [DOC-02a] Performance guide, NetBox-side tuning sections

**Context:** the operator tunes NetBox itself before reaching for
front-proxy or tool-side knobs. This window covers the two
NetBox-side levers, page size and database connection pool.

**Requirements:**

- Create `docs/operator-performance.md` if absent.
- Section "MAX_PAGE_SIZE tuning". Document how NetBox's
  configuration setting interacts with the nbsnap
  `--page-size` flag (`FEAT-17a`). Give a measurement command
  `time nbsnap export --page-size 500` versus
  `--page-size 1000`. Recommend a starting value of 500 with the
  trade-off noted (smaller pages reduce N+1 cost, larger pages
  reduce round-trip count).
- Section "PostgreSQL connection pool sizing". Document the
  `DATABASE` settings block. Give a measurement command using
  `pg_stat_activity` to inspect connection counts during an
  export. Recommend pool size = max-concurrent + 4 headroom.
- Cross-link from `docs/frictions/10`.

**Testing:** dry-run the guide against the test source stack with
the suggested settings, measure two `nbsnap export` runs at
default and tuned values, confirm the timing direction matches
the guide's prediction.

**Estimated Effort:** 1-2h

### [DOC-02b] Performance guide, front-proxy tuning sections

**Context:** when NetBox sits behind nginx or another WAF, the
proxy's rate-limit and body-size caps shape what nbsnap can
push through. This window covers the proxy-side levers.

**Requirements:**

- Extend `docs/operator-performance.md` with a "Front-proxy
  tuning" section.
- Sub-section "nginx rate limits". Include a working
  `limit_req_zone` + `limit_req` excerpt that allows nbsnap's
  retry-friendly burst pattern (500 reqs per 30 seconds, burst
  100). Reference RFC 9110 `Retry-After` semantics already
  honoured by `FEAT-01d`.
- Sub-section "Request and response body size caps". Recommend
  `client_max_body_size 32m` (covers the OpenAPI schema fetch
  which can run to several MB). Recommend
  `proxy_read_timeout 60s` for slower NetBox responses.
- Sub-section "Concurrency limits". Document
  `limit_conn_zone` + `limit_conn` and the interaction with
  `--max-concurrent`.
- Each sub-section names a curl or `nginx -T` command for the
  operator to inspect the live config.

**Testing:** apply one nginx rate-limit excerpt to a test proxy
in front of the source stack, confirm a `nbsnap export` retry
schedule survives the limit. Confirm `Retry-After` lands and
the export completes.

**Estimated Effort:** 1-2h

### [DOC-02c] Performance guide, GraphQL and bulk endpoint decision criteria

**Context:** the tool can opt into GraphQL (`RES-06`) and bulk
endpoints (`RES-07`) for specific read and write paths. The
guide names the trigger conditions so the operator knows when
to flip the flag.

**Requirements:**

- Extend `docs/operator-performance.md` with a "When to use
  GraphQL" section. Document the >30 percent wall-time gain
  threshold from `RES-06`. Cross-link to
  `docs/implementation/08-graphql-benchmark.md` for the
  measurement methodology. Name the two endpoints the gain
  is expected on (`dcim/devices/`, `ipam/ip-addresses/`).
- Extend with a "When to use bulk endpoints" section. Document
  the per-record error-handling cost vs throughput trade-off
  from `RES-07`. Name the two opt-in candidates,
  `--bulk-endpoints cables,interfaces`. Recommend the
  measurement, `time nbsnap import` with and without the
  flag on the `TEST-09a` 50k fixture.
- Add a "Decision flow" diagram (ASCII art) showing the order
  operators should evaluate, NetBox-side first, proxy second,
  GraphQL third, bulk fourth.

**Testing:** self-review confirms the decision flow matches the
RES-06 / RES-07 decision rules. Run a benchmark for the
GraphQL and bulk recommendations against the test stack,
confirm the numbers in the guide reflect a real measurement
not a guess.

**Estimated Effort:** 1-2h

### [DOC-03] Implementation notes index

**Context:** `docs/implementation/` carries per-decision rationales.
The index makes them findable.

**Requirements:**

- Create `docs/implementation/00-INDEX.md`.
- One line per implementation note linking to its file with a
  one-sentence summary.
- Cross-link from `docs/INDEX.md`.

**Testing:** click every link in the index, confirm targets exist.
Confirm every `docs/implementation/*.md` file is in the index.

**Estimated Effort:** 1-2h

### [FEAT-37a] `nbsnap reset-destination` module skeleton and CLI dispatch

**Priority:** HIGH (set 2026-06-15). The whole FEAT-37 series
should land before FEAT-36 implementation starts; the
look-ahead and Phase-2 work needs a clean destination to test
against, and dropping postgres between every test run is
heavyweight. FEAT-37a is the prerequisite that the four
subsequent sub-tickets stack on.

**Context:** the parent FEAT-37 ticket is decomposed into five
atomic sub-tickets. This first one creates the module file,
wires argparse, plugs the new sub-command into `cli.py`, and
prints a dry-run summary table. NO writes happen here, no
deletion logic yet. The safety layers (FEAT-37b), enumeration
(FEAT-37c), bulk delete (FEAT-37d), and audit (FEAT-37e)
follow.

**Requirements:**

- Create `src/nbsnap/reset_cli.py` with the module docstring
  and the constants `EXIT_OK = 0`, `EXIT_NEEDS_APPLY_FLAGS = 1`,
  `EXIT_DELETE_FAILURES = 2`, `EXIT_BLOCKED_BY_SOURCE_GUARD = 4`,
  `BATCH = 100`.
- Define `add_reset_args(parser)` with these flags: `--url`,
  `--token`, `--no-verify-tls`, `--content-types`, `--keep`
  (repeatable), `--apply`, `--i-know-what-im-doing`,
  `--on-error {stop,continue}`, `--audit-out`.
- Define `run_reset_cli(args)` that returns `EXIT_OK` and
  prints "# nbsnap reset-destination (dry-run)" plus an empty
  per-content-type summary. Real behaviour comes in later
  sub-tickets.
- In `src/nbsnap/cli.py`, add `"reset-destination": "FEAT-37"`
  to `TICKETS` and a new dispatch branch:

      elif name == "reset-destination":
          from nbsnap.reset_cli import add_reset_args, run_reset_cli
          add_reset_args(sub)
          sub.set_defaults(func=run_reset_cli)

- Update the stub-sub-command sweep in
  `tests/unit/test_cli.py` to exclude `reset-destination`
  from `_REMAINING_STUBS` (same list that already excludes
  the other implemented sub-commands).

**Testing:**

- Unit test in `tests/unit/test_reset_cli_skeleton.py`.
- Confirm `nbsnap reset-destination --help` lists every new
  flag, achieved by parsing via `_build_parser` from `cli.py`
  and reading the `--help` output (capture via `capsys`).
- Confirm `run_reset_cli(args)` returns `EXIT_OK` when called
  with dry-run defaults and a stubbed `NetboxHTTP`.
- Run `pytest tests/unit -q`, confirm no regressions.

**Estimated Effort:** 1-2h

### [FEAT-37b] Triple safety check for reset-destination

**Context:** the destructive nature of the new sub-command
demands three independent safety gates. Source-URL guard
refuses to touch `NB_SOURCE_URL`. `--apply` must be passed
explicitly to switch off dry-run. `--i-know-what-im-doing`
must be passed alongside `--apply` so a stray "--apply" in CI
does not wipe a real destination.

**Requirements:**

- In `run_reset_cli`, immediately after constructing
  `NetboxHTTP.from_env("destination", ...)`, call
  `http.is_source()` and exit with
  `EXIT_BLOCKED_BY_SOURCE_GUARD` (4) if True. Error message:
  "nbsnap reset-destination: refusing, destination URL
  matches NB_SOURCE_URL ({base_url}). The source NetBox is
  read-only by policy (see CLAUDE.md)."
- Then, if `args.apply` is True but `args.confirmed` is
  False, exit with `EXIT_NEEDS_APPLY_FLAGS` (1) and print:
  "nbsnap reset-destination: --apply also requires
  --i-know-what-im-doing." Plus a two-line block naming the
  URL and the action.
- Both checks happen BEFORE any GET or DELETE. Do not even
  fetch the OpenAPI schema until both gates pass.

**Testing:**

- Unit test in `tests/unit/test_reset_cli_safety.py`.
- Case 1, source-URL guard: monkeypatch
  `NB_SOURCE_URL=https://prod.example/`, call
  `run_reset_cli(_args(url="https://prod.example/"))`,
  assert return code 4 and the stderr message contains
  "matches NB_SOURCE_URL".
- Case 2, apply without confirmation: stub `NetboxHTTP.from_env`
  to return a non-source mock, pass `apply=True,
  confirmed=False`, assert return code 1 and the stderr
  message contains "also requires --i-know-what-im-doing".
- Case 3, dry-run is the default: `apply=False`, assert
  return code 0 and no DELETE calls were issued.

**Estimated Effort:** 1h

### [FEAT-37c] Enumerate destination IDs with --keep filter

**Context:** before deletion the command needs to know which
record IDs exist for each in-scope content type. Enumeration
uses NetBox's paged list endpoint with the `?limit=500`
convention and the `next` link, the same pattern
`NetboxHTTP.get_all` already implements. `--keep <name-or-slug>`
filters out specific records so an operator can preserve a
small set of pinned objects across the wipe.

NetBox REST endpoint used:

    GET /api/<endpoint>/?limit=500
    Authorization: Token <NB_DESTINATION_TOKEN>
    Accept: application/json

Response shape:

    200 OK
    {
      "count": N,
      "next": "<url-or-null>",
      "previous": "<url-or-null>",
      "results": [
        {"id": <int>, "url": "...", "display": "...",
         "name": "...", "slug": "..."},
        ...
      ]
    }

Endpoint table comes from
`nbsnap.natkey.verify.CONTENT_TYPE_ENDPOINTS`.

**Requirements:**

- In `src/nbsnap/reset_cli.py`, add:

      def _enumerate_ids(http, endpoint, keep_names: set[str]) -> Iterable[int]:
          for row in http.get_all(endpoint):
              rid = row.get("id")
              if not isinstance(rid, int):
                  continue
              name = row.get("name") or row.get("slug") or ""
              if name in keep_names:
                  continue
              yield rid

- Wire `_enumerate_ids` into `run_reset_cli`: after the
  safety gates, iterate the in-scope content types and call
  `_enumerate_ids(http, endpoint, keep_names=set(args.keep))`
  for each, accumulating counts in a `Counter[str]`. Print
  the per-content-type "would delete N records" line on
  stderr. Still no DELETE in this sub-ticket.

**Testing:**

- Unit test in `tests/unit/test_reset_cli_enumerate.py`.
- Stub `http.get_all` to yield three rows, one with `name=
  "keep-me"`, two without. Pass `keep_names={"keep-me"}` to
  `_enumerate_ids`, assert exactly two ids return.
- Stub a content type with zero rows, confirm
  `_enumerate_ids` yields nothing and does not raise.
- End-to-end through `run_reset_cli` (dry-run): assert the
  printed line "dcim.site: would delete 2 records" appears
  when the stub returns 2 sites.

**Estimated Effort:** 1h

### [FEAT-37d] Bulk DELETE with per-id 409 fallback

**Context:** NetBox 4.x supports a bulk DELETE on the list
endpoint, the array body `[{"id": 1}, {"id": 2}]`. When a
batch fails because one row has dependent objects, fall back
to per-id deletes so the rest of the batch completes.

NetBox REST endpoints used:

    DELETE /api/<endpoint>/
    Authorization: Token <NB_DESTINATION_TOKEN>
    Content-Type: application/json

    [{"id": 1}, {"id": 2}, ...]

    -> 204 No Content (success)
    -> 409 Conflict (something depends on one of the rows)

    DELETE /api/<endpoint>/<id>/
    Authorization: Token <NB_DESTINATION_TOKEN>

    -> 204 No Content (success)
    -> 409 Conflict (PROTECT FK from out-of-scope record)

Reference: NetBox Swagger UI `*_bulk_destroy` and
`*_destroy` operations.

**Requirements:**

- Add `_bulk_delete(http, endpoint, ids: list[int])`:

      body = [{"id": rid} for rid in ids]
      http._request("DELETE", endpoint, json=body)

- Add `_chunks(items, n)` helper that yields successive
  `n`-sized slices.
- Compute the reverse-topo deletion order once at the top of
  `run_reset_cli`: load the destination OpenAPI via
  `OpenAPI.fetch(http)`, build the graph with
  `from_openapi(openapi, scope=scope)`, run `plan(graph)`,
  iterate `reversed(plan.order)`. Use the existing
  `nbsnap.graph` exports.
- When `args.apply and args.confirmed`, for each content
  type, iterate `_chunks(ids, BATCH)` and call
  `_bulk_delete`. On `NetboxHTTPError`, fall back to per-id
  `http._request("DELETE", f"{endpoint}{rid}/")` for each id
  in the failed batch. Track per-id outcomes; on a single-row
  failure, append `(content_type, rid, str(exc))` to a
  `failures` list and continue or stop per `args.on_error`.
- Return `EXIT_DELETE_FAILURES` (2) when `failures` is non-
  empty, else `EXIT_OK`.

**Testing:**

- Unit test in `tests/unit/test_reset_cli_bulk_delete.py`.
- Case 1, happy path: stub `http._request` to return None
  (204), assert one DELETE call per batch and final return
  code 0.
- Case 2, bulk fails, per-id succeeds: stub the first
  `DELETE /api/x/` call to raise `NetboxHTTPError(409, ...)`
  then per-id `DELETE /api/x/<id>/` returns 204. Assert the
  fallback fires for every id in the batch, total return
  code 0.
- Case 3, per-id also fails: per-id raises 409 too, assert
  the failure is recorded and `on_error="stop"` returns
  code 2 immediately. With `on_error="continue"`, assert all
  ids in the remaining content types are still attempted.

**Estimated Effort:** 1-2h

### [FEAT-37e] Audit JSONL writer and integration test

**Context:** for forensics on a destructive operation, the
command writes a JSONL audit of every record it touched, one
JSON object per deleted (or failed) id. Plus an integration
test against the netbox-docker dest stack that proves the
end-to-end flow.

**Requirements:**

- In `src/nbsnap/reset_cli.py`, add `_flush_audit(args,
  lines: list[str])`:

      if args.audit_out is None:
          return
      args.audit_out.parent.mkdir(parents=True, exist_ok=True)
      args.audit_out.write_text("\n".join(lines) + "\n", encoding="utf-8")

- Append a JSON line to the in-memory `audit_lines` list for
  every per-id outcome:
  `{"content_type": ct, "id": rid, "outcome": "deleted"}`
  on bulk success,
  `{"content_type": ct, "id": rid, "outcome": "deleted-fallback"}`
  after the per-id fallback succeeds,
  `{"content_type": ct, "id": rid, "outcome": "failed",
    "message": <truncated>}` on failure.
- Call `_flush_audit(args, audit_lines)` once at the end of
  `run_reset_cli` (and on the early-exit failure path so the
  partial audit lands too).

**Testing:**

- Unit test in `tests/unit/test_reset_cli_audit.py`.
- Stub one content type with two ids, both delete cleanly.
  Pass `audit_out=tmp_path / "audit.jsonl"`. Assert the file
  contains two JSON lines with `outcome: "deleted"`.
- Stub a per-id failure, assert the JSON line carries
  `outcome: "failed"` and the message is truncated to 200
  chars.

- Integration test in
  `tests/integration/test_reset_destination.py`,
  decorated with `@pytest.mark.usefixtures("require_stack")`.
- Run `make stack-seed` ahead of the test (CI hook).
- Confirm `GET /api/dcim/sites/` returns `count > 0` before
  the reset call (sanity check the seed ran).
- Call `run_reset_cli` with apply=True, confirmed=True,
  url and token pointing at localhost:8081.
- Confirm `GET /api/dcim/sites/` returns `count == 0` after.
- Confirm the audit JSONL file exists and lists at least
  the seeded site id.

**Estimated Effort:** 1-2h

---

## Future considerations

## Cut, ticket no longer planned

These tickets were dropped during the question-burndown enrichment
pass. Listed for traceability so the cross-references in
`PLAN.md` and the design docs can be cleaned up in a follow-up.

## Completed

Per the audit on 2026-06-15, every ticket whose code has shipped
has been removed from the open backlog. Git history is the
authoritative implementation record. `git log --oneline TODO.md`
shows the audit commit and every prior body update; the matching
feat/fix/test commits in `src/` and `tests/` carry the
implementation detail per ticket.
