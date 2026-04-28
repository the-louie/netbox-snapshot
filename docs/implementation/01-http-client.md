# RES-01, HTTP client library decision

Status: **Decided**, 2026-06-14.

Implements the `RES-01` ticket in `TODO.md`. Every `FEAT-01*`
ticket consumes this choice, so the decision is locked before any
client code lands.

## Candidates

We surveyed three options that have a real chance of carrying
`nbsnap` through Phase 9.

| Candidate | Dependency cost | Type stubs | HTTP/2 | Async path | Retry hooks |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `requests` | one pinned wheel, ~500 KB, ubiquitous | `types-requests`, mature | no | none, would need parallel async port | adapter-level `urllib3.Retry`, coarse but workable |
| `httpx` | one pinned wheel, ~1 MB | first-class, ships with project | yes (opt-in via `h2`) | same API, sync or async | hookable `Transport` subclasses, clean |
| stdlib `urllib.request` | zero | stdlib | no | no | none, hand-rolled |

The `nb2kea` reference uses `curl` via `subprocess`. We are stepping
away from that because curl process spawn dominates the per-call
budget once we move past a handful of pages.

## Trade-off summary

* **Ubiquity and ops-ready installs.** `requests` is on every
  Python image our operators run, including the Debian-managed
  Python the project's test image uses where `pip install` is
  blocked by PEP 668. `httpx` is not always pre-installed and the
  added install step matters in air-gapped environments.
* **Timeout precision.** `requests` accepts a `(connect, read)`
  tuple, sufficient for our needs (NetBox calls do not need
  separate write or pool timeouts in v1).
* **Retry hooks.** `requests` exposes `urllib3.Retry` through a
  `HTTPAdapter`. The friction-10 retry envelope (curl-equivalent
  exits, HTTP 429, HTTP 5xx, with 0.5/1.5/3.0 schedule and a hard
  cap of 3) sits on top of that adapter. We additionally land a
  thin per-call retry loop for `Retry-After` parsing because
  `urllib3.Retry`'s `Retry-After` handling does not cleanly hand
  back our friction-10 semantics for HTTP-date forms.
* **HTTP/2.** Not required today. NetBox traditionally runs behind
  nginx, which speaks HTTP/2 to clients, but we issue one paged
  GET at a time so multiplexing does not move the needle in v1.
* **Type stubs.** `types-requests` is reliable and pinned in
  `[project.optional-dependencies].dev`.
* **TLS verify toggle.** Simple `verify=False` bool. Self-signed
  source endpoint stays opt-in.
* **Async migration path.** When `RES-02` flips to async (the
  measured trigger condition lives in `docs/implementation/02-runtime.md`),
  we will swap `requests` for `httpx.AsyncClient` and reuse the
  same `NetboxHTTP` façade. The cost of doing this later instead
  of now is a single transport rewrite, narrowly scoped to
  `src/nbsnap/http/client.py`.
* **Mocking story.** `responses` mocks `requests` calls cleanly
  and is widely available. We use it in unit tests.

## Decision

**Adopt `requests` (sync mode for v1).**

Reasoning, top three:

1. Available in every environment we run in, including the project's
   PEP-668-locked test image. Zero "install step blocked" surprises.
2. Retry envelope from `docs/frictions/10` lands cleanly as an
   `HTTPAdapter` plus a thin `Retry-After` wrapper.
3. Async is not a v1 need (`RES-02`). When it becomes one, we have
   a documented swap path to `httpx.AsyncClient`.

## What would force a flip

We would back out of `requests` and re-evaluate if any of the
following becomes true:

* The async trigger condition in `docs/implementation/02-runtime.md`
  fires (a measured export run that crosses 30 minutes wall-clock
  against the renderer-minimum data set).
* `requests` declares end-of-maintenance, or `urllib3` releases a
  breaking major that we cannot pin around.
* A NetBox-side streaming requirement appears that `requests`
  cannot meet without a contortion.

## Implementation handles

* Runtime dependency in `pyproject.toml` `[project].dependencies`:
  `requests>=2.31,<3` (the 2.31 floor pulls in the `urllib3` v2
  series so the retry adapter has the modern API).
* Tests pull `responses>=0.25` into the `dev` extras.

## Cross-references

* `PLAN.md` Phase 1, "HTTP client".
* `docs/frictions/10-api-scaling-and-rate-limits.md`, the retry
  schedule that this client must implement.
* `__reference/nb2kea/scripts/netbox_utils/netbox_common.py`, the
  curl-based reference behaviour we replicate.
* `docs/implementation/02-runtime.md`, the sync vs async decision
  that this choice supports for v1 and the trigger that would
  motivate a swap.
