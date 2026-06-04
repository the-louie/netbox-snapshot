# nbsnap operator performance guide

Four tuning levels in evaluation order. Always start with
NetBox-side knobs; only reach for front-proxy, GraphQL, or
bulk endpoints once the cheap wins land.

## Decision flow

```
NetBox-side first
   |
   v
Front-proxy tuning second
   |
   v
GraphQL opt-in third (read-side wins)
   |
   v
Bulk endpoints fourth (write-side wins)
```

If any layer's tuning yields under 10% wall-time gain on
your dataset, stop and use the next layer instead.

## NetBox-side tuning (DOC-02a)

### MAX_PAGE_SIZE

NetBox's `MAX_PAGE_SIZE` config setting caps how many rows
the destination will serve per request. nbsnap's
`--page-size` CLI flag chooses what to ask for. A larger
page reduces round-trip count; a smaller page reduces N+1
on linked resources.

```sh
time nbsnap export --page-size 500  --out a/
time nbsnap export --page-size 1000 --out b/
```

Recommendation: start at **500**. Raise to 1000 only when
the source NetBox runs on a fast database and the export
still feels slow.

### PostgreSQL connection pool

NetBox's `DATABASE` setting block controls the pool size.
Underprovisioned pools turn into 500-class errors during an
export.

```sql
-- as the postgres user against the netbox DB:
SELECT count(*) FROM pg_stat_activity WHERE datname = 'netbox';
```

Run that during a `nbsnap export`; if the count rises near
`max_connections`, raise the NetBox pool by the same
margin. Recommendation: pool size = `--max-concurrent` + 4
headroom.

Cross-link: `docs/frictions/10-known-gaps.md` notes the
specific symptoms.

## Front-proxy tuning (DOC-02b)

When NetBox sits behind nginx (or Caddy, Traefik), the
proxy's caps can throttle nbsnap before NetBox does.

### nginx rate limits

```nginx
limit_req_zone $binary_remote_addr zone=nbsnap:10m rate=500r/30s;

location /api/ {
    limit_req zone=nbsnap burst=100 nodelay;
    proxy_pass http://netbox;
    proxy_read_timeout 60s;
}
```

The `burst=100` budget covers nbsnap's retry pattern after
a transient 503; `Retry-After` (RFC 9110) is already
honoured by the HTTP client.

Inspect the live config:

```sh
ssh proxy nginx -T 2>/dev/null | grep -E 'limit_req|client_max_body_size'
```

### Body-size and timeouts

* `client_max_body_size 32m` — the OpenAPI schema fetch can
  exceed the nginx default 1 MB.
* `proxy_read_timeout 60s` — long-running `/api/dcim/devices/`
  reads under high concurrency.

### Concurrency limits

```nginx
limit_conn_zone $binary_remote_addr zone=nbsnap_conn:10m;

location /api/ {
    limit_conn nbsnap_conn 10;
}
```

Pair with `--max-concurrent 8` on the nbsnap side so the
client never exceeds the proxy's per-IP cap.

## When to use GraphQL (DOC-02c)

GraphQL is opt-in via `--use-graphql=read`. The decision
rule from `RES-06`: enable when GraphQL provides a >30%
wall-time gain on the two known-hot endpoints. The endpoints
in scope are `dcim/devices/` and `ipam/ip-addresses/`.

Measurement:

```sh
time nbsnap export --use-graphql=read  --out g/
time nbsnap export --use-graphql=off   --out r/
```

If the GraphQL run is within 30% of the REST run, stick with
REST — the GraphQL parser cost on the client wipes out the
network savings.

Cross-link: `docs/implementation/08-graphql-benchmark.md`
carries the methodology.

## When to use bulk endpoints (DOC-02c)

Bulk endpoints (`--bulk-endpoints cables,interfaces`) are
opt-in because per-record error handling becomes coarser. A
single bad row in a 100-row POST refuses the whole batch.

Decision rule from `RES-07`:

* Enable when the target endpoint has > 10,000 records AND
  the data has been validated with a previous regular run.
* Keep off for endpoints under 1,000 records; per-record
  error visibility outweighs the throughput gain.

Measurement:

```sh
time nbsnap import --bulk-endpoints cables,interfaces \
    --in tests/fixtures/scale-50k/snapshot/
time nbsnap import --in tests/fixtures/scale-50k/snapshot/
```

Compare the two wall-times; bulk should be at least 3x for
the opt-in to be worth it.
