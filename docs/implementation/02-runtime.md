# RES-02, sync vs async runtime model

Status: **Decided**, 2026-06-14.

Implements the `RES-02` ticket in `TODO.md`. The decision feeds
Phase 1 (`FEAT-01*`, the HTTP client) and Phase 8 (parallel-read
considerations).

## Context

`docs/05-export-import-workflow.md` defaults to single-worker
operation: one paged GET at a time, one PATCH at a time. Under
single-worker, async pays nothing because there is no overlap to
gain. The choice matters once we want parallel reads.

## Candidates

| Option | Cost today | Cost to migrate later |
| :--- | :--- | :--- |
| Sync v1 | low, idiomatic Python | swap transport when async is needed |
| Async v1 | medium, debugging is harder | none, already async |

The async tax is real:

* **Debugging complexity.** Stack traces span the event loop, which
  trips up developers reading them for the first time.
* **Context vars.** Logger contexts (trace ids, the run id) have to
  thread through `contextvars` rather than thread-local storage.
* **Exception group propagation.** PEP 654 lands cleanly in 3.11,
  but the call sites that fan out and gather work need careful
  exception-group handling that sync code does not.

## Decision

**Sync v1.** Single-worker, blocking calls, no event loop.

When the async trigger condition below fires, we plan a swap.
Until then sync is the floor: simpler to debug, simpler to test,
matches the developer audience.

## Migration path to async

The `NetboxHTTP` façade is the only public seam that hits the
network. When we go async:

1. Switch the runtime dependency from `requests` to
   `httpx>=0.27,<1` (the candidate that lost RES-01 on
   ubiquity grounds, not on technical ones).
2. Replace `requests.Session` with `httpx.AsyncClient` inside
   `NetboxHTTP._request`.
3. Convert `_request`, `get_one`, `get_all`, `post`, `patch` to
   `async def`.
4. The export / import drivers grow an `asyncio.gather` per phase.

Everything else (the planner, the natural-key resolver, the
manifest writer) is sync-pure today and stays sync. Async is a
transport concern, not a domain-logic concern.

The cost of doing this later instead of now is a single transport
rewrite, narrowly scoped to `src/nbsnap/http/client.py` plus the
driver fan-outs.

## Trigger condition (what would force async)

We will revisit when **any** of these is observed and measured (not
guessed):

* A full export against the renderer-minimum data set crosses 30
  minutes wall-clock on a 1 vCPU runner. (Today's targets in
  `PLAN.md` are 10 minutes for 5 000 objects.)
* A bulk import phase blocks on serial PATCH calls and the NetBox
  side reports queue depth headroom (so the limit is on our side,
  not theirs).
* The renderer-parity test consistently spends >50% of wall-clock
  in network I/O, measured with `PerfTimer` (FEAT-15b).

## httpx prototype sketch

The same call shape works in both sync and async, so the migration
is a swap rather than a rewrite. Sketch:

```python
# Sync, v1 with requests
def get_one(self, path: str) -> dict:
    r = self._session.get(self._url(path), timeout=self._timeout)
    r.raise_for_status()
    return r.json()

# Future async, with httpx.AsyncClient
async def get_one(self, path: str) -> dict:
    r = await self._client.get(self._url(path), timeout=self._timeout)
    r.raise_for_status()
    return r.json()
```

The body diff is the `await` keyword and the client object. The
public method signature is the only thing operators see; we accept
that one breaking change at the swap.

## Cross-references

* `PLAN.md` Phase 1, Phase 8.
* `docs/05-export-import-workflow.md`, single-worker default.
* `docs/implementation/01-http-client.md`, the `requests` choice
  this decision builds on.
