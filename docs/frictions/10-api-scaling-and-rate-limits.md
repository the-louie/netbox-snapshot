# 10 — API scaling, pagination, rate limits, and timeouts

## What the problem actually is

The snapshot tool is API-only by design (see `00-problem-statement.md`).
That has cost. A NetBox instance with tens of thousands of objects
imposes real limits that a `pg_dump` user never sees.

### Numbers to anchor on

A medium NetBox deployment that matches the reference project at scale:

* ~300 Devices.
* ~5,000 Interfaces (≥10 per device for switches; 24–48 for access
  switches).
* ~600 Cables.
* ~10,000 IPAddresses (per-interface + service IPs + reservations).
* ~50 VLANs, ~200 Prefixes, ~30 IP Ranges.

A *large* deployment (campus + DC + branches): 5–10× those numbers.
50,000+ IPAddresses is plausible. 100,000+ is not unheard of.

### Pagination

NetBox returns paginated results with a default page size of 50 and a
configurable maximum. Many endpoints honour `?limit=0` to mean "all in
one response" but:

* "All" can blow the page-render time budget on the server, producing
  a request that times out.
* Even when it succeeds, the response can be tens of MB JSON, which
  is slow to parse.
* On endpoints that hold custom data (Config Contexts with large JSON
  blobs), `limit=0` can produce a >100MB response — the operator's
  reverse proxy may cap body size.

### Rate limits

NetBox itself is **not rate-limited by default** but many operators
front it with nginx or an WAF that enforces:

* Per-IP requests-per-second caps.
* Per-token concurrent-connection caps.
* Body-size caps (1MB / 10MB are common).
* Total-bytes-per-minute caps.

A snapshot run that fires 200+ small GETs over a few seconds can trip
these. The exporter needs to be deliberate about cadence.

### Timeouts

NetBox responses for "big" queries (Devices with deeply nested
serialisers) can take 5–30s on a busy server. The `nb2kea` curl client
caps at 30s, which is reasonable for most calls but inadequate for an
unfiltered `dcim/interfaces/?limit=0` on a 50,000-interface install.

### Network-side issues

* TLS handshake cost adds up over thousands of separate connections.
* TCP slow-start hurts a fresh connection per request.
* DNS resolution flakiness mid-run is unrecoverable without retry.
* Authenticated session cookies expire mid-run.

### NetBox server-side issues

* Django ORM N+1 queries: some serialisers fetch related objects per
  row, so a `limit=1000` request makes 1000 inner queries. The
  symptom is that pagination *scales worse than linearly*. Smaller
  pages are sometimes faster overall.
* PostgreSQL connection saturation: if the operator runs many tools
  against NetBox at the same time, we're competing with whatever else
  uses the DB.

## Why it bites this project specifically

The exporter walks **every** object type. For a large instance, that
is hundreds of paginated GETs even with `limit=0` working. The
importer is worse: every write is a separate request, plus a
preceding GET to check the upsert path. A 10,000-IPAddress instance
needs 10,000 GETs and up to 10,000 POSTs (more if any PATCH).

If a NetBox upgrade or an overzealous WAF rule raises latency by 100ms
per request, the export run goes from minutes to hours.

The user's brief says nothing about scale, but a snapshot tool that
falls over at 5,000 interfaces is a bad tool.

## Mitigations

### M1 — Sensible default page sizes (chosen)

* Default page size: 500 (NetBox `MAX_PAGE_SIZE` is configurable;
  500 is below most operators' caps and avoids the worst N+1).
* Configurable via `--page-size` CLI flag.
* Auto-shrink on consecutive timeouts: if a `limit=500` request times
  out, retry with `limit=200`, then `limit=50`. Cache the working
  page size for the rest of the run.

### M2 — Follow `next`, never `limit=0`

`?limit=0` is a tempting "just give me everything" but produces the
biggest individual requests, the worst memory pressure, and the
longest tail. Always paginate and follow `next` links.

### M3 — Per-endpoint concurrency control

Single-worker by default (matches the design in
`03-dependency-graph.md`). For independent endpoints (no FK
dependency), the exporter *could* fetch in parallel — gated by
`--max-concurrent N`, default 1, max 4. The justification is small —
networking overlap helps modestly — and the risk of tripping a WAF
limit is real, so the default stays conservative.

### M4 — Bounded retry + exponential backoff for transient failures

Implementation library: `requests>=2.31,<3`, per
`docs/implementation/01-http-client.md` (RES-01). The retry envelope
lands as an `HTTPAdapter` plus a thin `Retry-After` wrapper so the
rules below stay close to the transport instead of leaking into
call sites.

Reuse the pattern from `__reference/nb2kea/scripts/netbox_utils/netbox_common.py`:

* Retry on curl exit 28 (timeout), HTTP 429, HTTP 5xx, or no-status
  (connection failure).
* Exponential backoff (0.5s, 1.5s, 3.0s).
* Cap at 3 retries per request.
* No retry on 4xx other than 429.

### M5 — Respect `Retry-After` headers

If NetBox or its front-proxy returns `Retry-After: <seconds>`, sleep
that long before retrying. Don't burn through retries fighting a WAF.

### M6 — Bulk endpoints for known-good types

NetBox supports bulk POST/PATCH on list endpoints by sending an
**array** of object dicts. We use bulk for:

* Cables (300–1000+ per medium instance).
* Interfaces during initial population.
* IPAddresses when the destination is empty.

We do **not** use bulk for:

* Devices (per-record error messages matter; cycle handling matters).
* Anything with deferred-FK two-phase apply (the deferred-FK PATCH is
  per-object).

### M7 — Resumable export and import

Both export and import write a `.progress` file in the snapshot
directory:

```jsonl
{"phase": "export", "endpoint": "dcim/interfaces/", "next_url": "https://.../api/dcim/interfaces/?limit=500&offset=2500"}
```

If the run is interrupted, the next invocation resumes from
`next_url`. The snapshot is therefore safe to interrupt — the cost of
restart is bounded by "since the last page-write".

### M8 — Field allowlisting via `?brief=true` for index passes

NetBox supports `?brief=true` on most list endpoints, returning a
minimal representation. We use this for the index passes in import
phase I2 (the natural-key index doesn't need full records), cutting
bandwidth and parse time by 5–10×.

NetBox API docs reference:
`https://docs.netbox.dev/en/stable/integrations/rest-api/`.

### M9 — GraphQL for the deep-nested reads

The most pagination-painful endpoints are the ones with deep nesting
(`/api/dcim/devices/` with `?include=interfaces,primary_ip4,...`).
NetBox's GraphQL endpoint can fetch only the fields we need in one
round trip. We do **not** use GraphQL for everything (the
write-back side is REST-only anyway), but for the export's
field-targeted read passes, GraphQL is a real win.

Defer to v1.1 to keep v1 simpler.

NetBox GraphQL docs:
`https://docs.netbox.dev/en/stable/integrations/graphql-api/`.

### M10 — Observability: per-endpoint timing in the manifest

The manifest carries:

```json
"performance": {
  "export_duration_seconds": 143.2,
  "endpoint_timings": {
    "dcim/interfaces/": {"requests": 11, "total_seconds": 78.4, "max_seconds": 8.1},
    "ipam/ip-addresses/": {"requests": 22, "total_seconds": 41.2, "max_seconds": 4.0}
  }
}
```

So the operator can see at-a-glance what endpoints are slow and
whether a NetBox upgrade or load shed will help.

### M11 — Verify content-length matches expectations

NetBox 4.x always returns a `count` in paginated list responses. The
exporter computes "expected total records" upfront and asserts the
actual rows collected matches. A mismatch means a page was dropped
silently — surface it.

### M12 — Operator-side performance runbook

Bundle a small `docs/operator-performance.md` (future) that documents:

* How to raise NetBox's `MAX_PAGE_SIZE` if the operator wants more
  throughput.
* How to configure WAF/nginx to allow the exporter through.
* How to gate the importer behind a maintenance window.

## References

* NetBox API pagination docs:
  `https://docs.netbox.dev/en/stable/integrations/rest-api/`
  (search "pagination" and "MAX_PAGE_SIZE")
* NetBox GraphQL docs:
  `https://docs.netbox.dev/en/stable/integrations/graphql-api/`
* NetBox bulk operations (POST a list):
  `https://docs.netbox.dev/en/stable/integrations/rest-api/`
  (search "bulk")
* HTTP `Retry-After` semantics:
  `https://datatracker.ietf.org/doc/html/rfc9110#name-retry-after`
* curl write-out / retry conventions (reference implementation in this
  repo): `__reference/nb2kea/scripts/netbox_utils/netbox_common.py`
* nginx rate-limiting directives commonly applied to NetBox:
  `https://nginx.org/en/docs/http/ngx_http_limit_req_module.html`
* PostgreSQL connection pool tuning for Django/NetBox:
  `https://docs.djangoproject.com/en/5.0/ref/databases/#persistent-connections`
* NetBox issue: slow serialiser for nested `/dcim/devices/` —
  gsearch: `site:github.com/netbox-community/netbox slow devices
  serializer N+1`
* gsearch: `netbox API pagination MAX_PAGE_SIZE timeout`
* gsearch: `pynetbox bulk create performance`
* gsearch: `django rest framework N+1 select_related prefetch_related`
* gsearch: `netbox GraphQL vs REST performance large query`
