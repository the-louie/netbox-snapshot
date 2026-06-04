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



**Estimated Effort:** 1-2h. Depends on REFACTOR-03a.



---


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
