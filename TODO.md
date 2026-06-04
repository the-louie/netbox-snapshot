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
