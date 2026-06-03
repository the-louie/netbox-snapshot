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



**Estimated Effort:** 1-2h. Depends on REFACTOR-03a.



---


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
