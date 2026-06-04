# nbsnap operator runbook

This runbook covers the three supported deployment workflows
for moving a NetBox network model between instances via
nbsnap. The Safety section below is the canonical reference
for the read-only invariant — every workflow links back to
it before any command runs.

## Safety

> **The source NetBox at `NB_SOURCE_URL` is production and
> read-only.** Every interaction with it must be `GET`-only.
> The `nbsnap export` command is the only sanctioned way to
> touch the source; the `NetboxHTTP` client refuses non-GET
> requests when its `base_url` matches `NB_SOURCE_URL`, so
> even a typo cannot land a write.
>
> Writes flow source → snapshot file → destination. The
> destination at `NB_DESTINATION_URL` is the only NetBox
> that ever receives writes from this tool. See `CLAUDE.md`
> for the full banner; this section is a working copy.

If at any point you discover that a command was pointed at
the source URL by mistake, treat it as a production
incident: stop, document, surface to the operator on call.

Required env vars before any workflow below:

```sh
export NB_SOURCE_URL="https://host.docker.internal:8443"
export NB_SOURCE_TOKEN="…"     # read-only token
export NB_DESTINATION_URL="https://netbox.i.louie.se"
export NB_DESTINATION_TOKEN="…"  # write token
```

## Workflow 1 — Cold migration (DOC-01a)

*see Safety section above*

A fresh, empty destination NetBox absorbs a complete copy of
the source's network model. The destination has been
installed but is otherwise blank.

1. Confirm the destination is empty:
   ```sh
   curl -sk "$NB_DESTINATION_URL/api/dcim/sites/?limit=1" \
       -H "Authorization: Token $NB_DESTINATION_TOKEN" | jq '.count'
   # expect 0
   ```
2. Preflight (offline check; nbsnap fetches the destination's
   `/api/status/` and `/api/schema/`):
   ```sh
   nbsnap import \
       --url "$NB_DESTINATION_URL" --token "$NB_DESTINATION_TOKEN" \
       --in ./snapshot-source-frozen/ \
       --dry-run
   ```
   Confirm `preflight version skew: NONE` and no
   `schema drift:` entries. If drift is present, decide
   whether to proceed (operator call) or refresh the
   snapshot via the `nbsnap-export` skill.
3. Export from source (read-only):
   ```sh
   nbsnap export \
       --url "$NB_SOURCE_URL" --token "$NB_SOURCE_TOKEN" --no-verify-tls \
       --out ./snapshot/
   ```
4. Import to destination:
   ```sh
   nbsnap import \
       --url "$NB_DESTINATION_URL" --token "$NB_DESTINATION_TOKEN" \
       --in ./snapshot/ \
       --on-error continue \
       --audit-out ./audit.jsonl
   ```
   Exit code 0 means a clean run. Exit codes 1-7 are
   documented in `nbsnap import --help`.
5. Verify counts:
   ```sh
   for ct in dcim/sites dcim/devices ipam/prefixes ipam/ipaddresses; do
       src=$(curl -sk "$NB_SOURCE_URL/api/$ct/?limit=1" \
           -H "Authorization: Token $NB_SOURCE_TOKEN" | jq '.count')
       dst=$(curl -sk "$NB_DESTINATION_URL/api/$ct/?limit=1" \
           -H "Authorization: Token $NB_DESTINATION_TOKEN" | jq '.count')
       printf "%s\tsrc=%s\tdst=%s\n" "$ct" "$src" "$dst"
   done
   ```

**Rollback**: the destination was empty; reinstall NetBox
or `psql -d netbox -c "TRUNCATE ... CASCADE"` the affected
tables. Rollback against the production source is
out-of-scope by design.

## Workflow 2 — Parallel deployment (DOC-01b)

*see Safety section above*

Two sibling NetBoxes serve different sites and need to
share a network-model baseline. Install-local data
(`IPAddress.dns_name` carrying the source's own hostname)
must be reviewed before the import runs against the
destination.

1. Run a normal export (Workflow 1 step 3).
2. Inspect the install-local flags file
   `snapshot/flags.jsonl`. Each entry is a `{path, finding}`
   pair where `finding` is one of `source-host-dns_name`,
   `local-receiver-url`, etc.
3. Three operator choices per entry:
   * **Keep** — the entry is genuinely network-modelled
     even though the source's hostname appears (rare).
   * **Drop** — `nbsnap import --skip path:<jsonl path>`
     leaves the field at the destination's default.
   * **Rewrite** — `nbsnap import --replacement-map
     <yaml>` substitutes the source hostname for the
     destination's. The yaml file maps
     `source-host -> dest-host`.
4. The `--allow-source-install-ips` flag short-circuits the
   block. Acceptable only when the operator has reviewed
   every flagged entry and decided the source's hostname
   is harmless on the destination (a parallel staging
   environment that shares the source's resolver). Default
   off; document the reason in a commit message when set.

## Workflow 3 — Partial re-sync (DOC-01c)

*see Safety section above*

The source has changed since the last cold migration; the
destination needs the delta only. nbsnap does a full
re-export each time (the format is cheap to diff) and the
import patches existing rows in place.

1. Re-export:
   ```sh
   nbsnap export --url "$NB_SOURCE_URL" --token "$NB_SOURCE_TOKEN" \
       --no-verify-tls --out ./snapshot-resync/
   ```
2. Import with the default `--reject-existing off`:
   ```sh
   nbsnap import --url "$NB_DESTINATION_URL" --token "$NB_DESTINATION_TOKEN" \
       --in ./snapshot-resync/ --on-error continue \
       --audit-out ./resync.audit.jsonl
   ```
3. Inspect the run summary; the `updated:` count is the
   delta size. `noop:` is the unchanged majority.
4. Grep the audit for PATCHED outcomes:
   ```sh
   jq -c 'select(.category == "patched")' ./resync.audit.jsonl
   ```

## Exit codes

| Code | Meaning |
| :--- | :--- |
| 0 | Clean run. |
| 1 | `EXIT_PREFLIGHT_BLOCKED` — preflight refused; `--strict-schema` may have fired. |
| 2 | `EXIT_ROW_FAILURES` — Phase-1 or Phase-2 PATCH failed, or `MISSING_FROM_SOURCE` drops present. |
| 3 | `EXIT_BAD_INVOCATION` — malformed CLI args, missing env vars. |
| 4 | `EXIT_DESTINATION_UNREACHABLE` — TLS / network failure against destination. |
| 5 | `EXIT_UNEXPECTED` — uncaught error; please report. |
| 6 | `EXIT_SKIPPED_OVER_THRESHOLD` — `--max-skipped` or `--max-skipped-ct` tripped. |
| 7 | `EXIT_BYPASS_USED` — completed but rescued via `--allow-enum-dict-bypass`. |
