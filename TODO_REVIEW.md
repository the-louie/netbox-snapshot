# TODO review, enrichment-pass result

The 27-question burndown is complete and the enrichment pass has
folded every answer into `TODO.md`. This file's earlier role as a
question backlog has been replaced by the durable summary below. The
detailed answers are preserved in the "Answers archive" section at
the bottom for future maintainers.

## Per-ticket status, post-enrichment plus second decomposition pass

Every open ticket in `TODO.md` is now Ready (`R`) for a developer to
start without further clarification. The second decomposition pass
split six over-sized tickets into atomic 1 to 2 hour windows. The
table below reflects the final structure.

| Phase | Tickets | Status |
| :--- | :--- | :--- |
| 0, Foundation | `INFRA-01a..c`, `INFRA-02a..c`, `INFRA-03a..d, 03e..f, 03g1..2, 03h`, `INFRA-04a..d`, `RES-01..03` | All `R` |
| 1, Schema discovery | `FEAT-01a..f, 01g1..3`, `FEAT-02a..d`, `FEAT-03a..b`, `FEAT-04a..b`, `TEST-01a..b` | All `R` |
| 2, Graph construction | `FEAT-05a..b, 05c1..3`, `FEAT-06a..c`, `RES-04`, `FEAT-07a..b`, `TEST-02a..b` | All `R` |
| 3, Natural keys | `FEAT-08a, 08b1..3`, `FEAT-09a..c`, `FEAT-10a..b`, `TEST-03a..b` | All `R` |
| 4, Export engine | `FEAT-11a..d`, `FEAT-12a..c`, `FEAT-13a`, `FEAT-13c`, `FEAT-14a..b`, `FEAT-15a..b`, `FEAT-16a..b`, `FEAT-17a..b`, `TEST-04`, `TEST-05` | All `R` |
| 5, Import engine | `FEAT-18a..c`, `FEAT-19a..b`, `FEAT-20a..c`, `FEAT-21a..b`, `FEAT-22a..b`, `FEAT-23`, `FEAT-24a..b`, `FEAT-25a..b`, `TEST-06`, `TEST-07` | All `R` |
| 6, Verification | `FEAT-26a..b`, `FEAT-27a..b`, `TEST-08a1..3, 08b, 08c1..3` | All `R` |
| 7, Operational polish | `FEAT-28a..b`, `FEAT-29`, `FEAT-30`, `DOC-01a..c`, `DOC-02a..c`, `DOC-03`, `FEAT-34`, `FEAT-35` | All `R` |
| 8, Extensions | `FEAT-31a..b`, `FEAT-32`, `RES-06..08` | All `R` |
| 9, Hardening + release | `FEAT-33a1..2, 33b`, `TEST-09a1..3, 09b`, `REL-01a..b`, `REL-02` | All `R` |

## Third decomposition pass (latest)

Three more tickets split into atomic 1 to 2 hour sub-tickets,
net +6 tickets.

| Original ticket | Split into |
| :--- | :--- |
| `TEST-08c` | `TEST-08c1` (roundtrip orchestration), `TEST-08c2` (renderers against destination), `TEST-08c3` (diff with banner whitelist) |
| `DOC-02` | `DOC-02a` (NetBox-side tuning), `DOC-02b` (front-proxy tuning), `DOC-02c` (GraphQL/bulk decision criteria) |
| `TEST-09a` | `TEST-09a1` (Sites and Devices generator), `TEST-09a2` (Interfaces generator), `TEST-09a3` (IPs and Cables generator) |

Total ticket count, was 137, now 143 active (plus 2 in the Cut
section).

## Second decomposition pass

Six over-sized tickets split into atomic 1 to 2 hour sub-tickets,
net +10 tickets.

| Original ticket | Split into |
| :--- | :--- |
| `FEAT-01g` | `FEAT-01g1` (exception + helper), `FEAT-01g2` (NetboxHTTP integration), `FEAT-01g3` (e2e socket-mock test) |
| `FEAT-05c` | `FEAT-05c1` (OPTIONS probe), `FEAT-05c2` (dest-only POST fallback), `FEAT-05c3` (edge emission + cache) |
| `FEAT-08b` | `FEAT-08b1` (DCIM Sites through Cable), `FEAT-08b2` (DCIM ports + IPAM), `FEAT-08b3` (Tenancy + Extras) |
| `INFRA-03g` | `INFRA-03g1` (IPAM seed fixtures), `INFRA-03g2` (primary_ip4 patch step) |
| `TEST-08a` | `TEST-08a1` (Sites through Devices), `TEST-08a2` (Interfaces + IPAddresses), `TEST-08a3` (Cables + nb2kea verify) |
| `FEAT-33a` | `FEAT-33a1` (4 standard sections), `FEAT-33a2` (source-readonly invariant + ruff plugin) |

## What changed in the enrichment pass

### New tickets added (8)

| Ticket | Phase | Source |
| :--- | :--- | :--- |
| `FEAT-01g` | 1 | Q8 burndown, source read-only guard rail tests + `SourceWriteForbidden` |
| `INFRA-03e` | 0 | Q6 burndown, sites and locations seed |
| `INFRA-03f` | 0 | Q6 burndown, devices and interfaces seed |
| `INFRA-03g` | 0 | Q6 burndown, addressing seed |
| `INFRA-03h` | 0 | Q6 burndown, cabling seed |
| `FEAT-34` | 7 | Q4 burndown, `nbsnap pack` |
| `FEAT-35` | 7 | Q4 burndown, `nbsnap unpack` |
| `RES-08` | 8 | Q24 burndown, v1.1 source for renderer-parity dataset |

### Tickets cut (2)

| Ticket | Reason |
| :--- | :--- |
| `FEAT-13b` | Q17 / Q19 burndown, DNS resolution path eliminated by network-only scope |
| `RES-05` | Q17 / Q19 burndown, DNS decision is moot |

### Tickets narrowed or amended (15)

| Ticket | Change |
| :--- | :--- |
| `INFRA-01a` | License BSD-3-Clause, author/maintainer `Louie <louie@louie.se>`, ship `LICENSE` file |
| `INFRA-01c` | README opens with the production-read-only banner |
| `INFRA-02b` | Stubs route to `FEAT-17a/25a/07a/26b/27b/10b/34/35` |
| `INFRA-03a` | Tag pin language clarified, implementer picks latest 4.6.x at impl time |
| `FEAT-01a` | Constructor refuses non-GET when `base_url` matches source (layer 1) |
| `FEAT-01b` | `_request` envelope refuses non-GET on source clients (layer 2) |
| `FEAT-01d` | Retry-After honours both integer-seconds and HTTP-date formats |
| `FEAT-02b` | Endpoint-to-content-type via URL convention plus curated exceptions |
| `FEAT-02c` | FK target detection via `$ref` plus schema-name pattern plus curated table |
| `FEAT-02d` | Allowlist is union of POST request body and PATCH request body |
| `FEAT-03a` | Probe `content-types/` then `object-types/` |
| `TEST-01b` | Informational not gating, log INFO either way |
| `FEAT-05c` | Runtime OPTIONS probe, dest-only fallback, raises `PlannerRequiresDestination` on source |
| `FEAT-08b` | In-scope set is DCIM + IPAM + Tenancy, decorating Extras |
| `FEAT-12b` | M2M output sorted by natural key |
| `FEAT-13a` | Narrowed to single rule `MATCHES_SOURCE_NETBOX_HOST` on `IPAddress.dns_name` |
| `FEAT-15a` | Manifest exclusions is hybrid `scope + opt_in` shape |
| `FEAT-17a` | Drops `--include-password-hashes`, `--include-journal`, `--source-db-url`, `--resolve-webhook-urls` |
| `FEAT-18c` | Walks every CF, `is_blocking` flag derived from in-scope intersection |
| `FEAT-20b` | M2M sorted by destination id, normalised for upsert compare |
| `FEAT-21b` | Skip-if-equal normalises M2M via shared helper |
| `FEAT-25a` | Drops `--include-password-hashes`, `--source-db-url` |
| `FEAT-27b` | Hard-refuses to run against `NB_SOURCE_URL` |
| `TEST-08a` | Hand-built synthetic fixture, v1.1 source deferred to `RES-08` |
| `DOC-01a/b/c` | Open with shared Safety section, per-workflow link |
| `FEAT-31a` | Runtime WARNING on out-of-scope content type registration |
| `FEAT-31b` | Network-only scope rule documented in extension contract |
| `FEAT-33a` | Both static and runtime self-tests for source read-only invariant |
| `INFRA-04b` | Confirmed Python 3.11 + 3.12 matrix |
| `FEAT-13c` | Stays as flag-file writer, still consumed by the narrowed `FEAT-13a` |

## Awaiting downstream cleanup

The cross-references in `PLAN.md`, `docs/INDEX.md`, `docs/02-data-model-scope.md`,
and the friction docs were not touched in this enrichment pass since
they remain conceptually correct. A follow-up pass should:

- Update `goals.md` Layer 2 list to align with the Q16 in-scope set
  (drop Users, Groups, ObjectPermissions, Webhooks, Config
  Contexts, etc.).
- Update `docs/01-scope.md` and `docs/02-data-model-scope.md` to
  match the Q16 content type table.
- Update `docs/03-dependency-graph.md` to mention the OPTIONS-based
  polymorphic discovery from Q15.
- Strike through or remove references in the friction docs (`03`,
  `07`, `08`) to dropped objects.
- Cross-link `PLAN.md` Phase 1 scope to `FEAT-01g`.

These are documentation hygiene tasks, none gate `INFRA-01a` from
starting today.

## Answers archive

The full Q1 through Q27 answer rationale follows, preserved verbatim
from the question-burndown phase.

### Q1, Project license (ANSWERED, BSD-3-Clause)

`pyproject.toml` `[project].license` = `BSD-3-Clause`. `LICENSE`
file at repo root with the SPDX text.

### Q2, Author and maintainer (ANSWERED, Louie <louie@louie.se>)

`INFRA-01a` writes `Louie <louie@louie.se>` into both
`[project].authors` and `[project].maintainers`.

### Q3, README warning prominence (ANSWERED, Banner at top of README)

README opens with the same production-read-only banner as
`CLAUDE.md`, rendered as the first content after the H1 title.

### Q4, pack and unpack sub-commands (ANSWERED, Ship in v1, FEAT-34 + FEAT-35)

Ship in v1, two new tickets drafted.

### Q5, netbox-docker version pin (ANSWERED, Latest 4.6.x at impl time)

Implementer picks the highest 4.6.x tag available when `INFRA-03a`
is worked, pins via `NETBOX_DOCKER_TAG` Make variable, records the
choice in `tests/fixtures/README.md`.

### Q6, Test stack seed minimum content (ANSWERED, Per-group tickets INFRA-03e..h)

Four new tickets `INFRA-03e` (sites/locations), `INFRA-03f`
(devices/interfaces), `INFRA-03g` (addressing), `INFRA-03h` (cabling).

### Q7, Python version matrix (ANSWERED, 3.11 and 3.12)

CI matrix is 3.11 + 3.12.

### Q8, Source read-only guard rail placement (ANSWERED, Both layers)

Constructor refuses non-GET on source-URL match. `_request`
envelope refuses non-GET on source clients. URL match is
host-and-port substring.

### Q9, Retry-After HTTP-date format (ANSWERED, Both formats)

Parse integer seconds first, fall back to HTTP-date via
`email.utils.parsedate_to_datetime`.

### Q10, Endpoint to content-type mapping (ANSWERED, Convention + curated exceptions)

URL convention with hyphen-aware singularisation, curated
exceptions table for irregulars.

### Q11, FK target detection (ANSWERED, $ref + pattern + table)

Three-layer, `$ref`, `BriefX`/`NestedX` pattern, curated exception
table.

### Q12, Write allowlist source (ANSWERED, Union of POST and PATCH)

Allowlist is the union, per-verb subsets used by the importer.

### Q13, Content-type endpoint name (ANSWERED, Probe both endpoints)

`ContentTypeCache.fetch` tries `content-types/`, falls back to
`object-types/` on 404.

### Q14, Forcing CT id divergence in tests (ANSWERED, Informational, not gating)

`TEST-01b` logs INFO either way, passes regardless.

### Q15, Polymorphic FK union of targets (ANSWERED, Runtime API probe)

Runtime discovery via `OPTIONS` preferred, dest-only dry-run-POST
fallback. Source-bound client raises `PlannerRequiresDestination`.

### Q16, In-scope content type list (ANSWERED, DCIM + IPAM + Tenancy)

In-scope, DCIM + IPAM + tenancy.tenant + tenancy.tenantgroup +
decorating CFs/ChoiceSets/Tags. Out-of-scope, everything else
including users, contacts, webhooks, config contexts, journal.

### Q17, Install-local classifier scope (ANSWERED, Narrowed to one rule)

`FEAT-13a` narrows to `MATCHES_SOURCE_NETBOX_HOST` on
`IPAddress.dns_name`. `FEAT-13b` and `RES-05` cut.

### Q18, M2M ordering (ANSWERED, Sorted by natural key)

Export sorts by natural-key tuple, import sorts by destination id,
upsert compares through a normaliser.

### Q19, DNS resolution opt-in (AUTO-ANSWERED via Q17, Dropped)

Cut along with `FEAT-13b` and `RES-05`.

### Q20, Manifest reshape (ANSWERED, Hybrid scope + opt-in slot)

`exclusions = {scope: "network-only", opt_in: {}}`.

### Q21, CLI flags to drop (ANSWERED, Remove all three)

`--include-password-hashes`, `--include-journal`, `--source-db-url`
all dropped from both export and import CLIs.

### Q22, CF reconciliation scope (ANSWERED, Walk all CFs, blocking flag)

`FEAT-18c` walks every CF, `is_blocking` derived from in-scope
intersection.

### Q23, Roundtrip CLI refuse production (ANSWERED, Hard refuse)

`FEAT-27b` exits non-zero before any HTTP call when `--source-url`
matches `NB_SOURCE_URL`.

### Q24, Reference dataset source (ANSWERED, Hand-built synthetic for v1)

`TEST-08a` ships a hand-designed fixture. `RES-08` picks the v1.1
source.

### Q25, Operator runbook safety language (ANSWERED, Shared Safety section + per-workflow link)

Runbook opens with a Safety section, each workflow opens with a
"see Safety section above" link.

### Q26, Plugin extension scope (ANSWERED, Documented rule + runtime warning)

Contract document declares the rule, runtime emits a WARNING per
out-of-scope registration.

### Q27, Security review self-test (ANSWERED, Both static + runtime)

`FEAT-33a` ships a static grep + ruff custom rule plus a runtime
audit-log scan.
