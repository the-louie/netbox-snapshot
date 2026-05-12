# NetBox Portable Snapshot (working title)

> ## ABSOLUTELY NO CHANGES ARE ALLOWED ON THE SOURCE NETBOX
>
> The instance at `NB_SOURCE_URL` (`host.docker.internal:8443`) is the
> **production** NetBox. Every interaction with it must be **read-only**.
>
> - **No `POST`, `PATCH`, `PUT`, or `DELETE`** requests against
>   `NB_SOURCE_URL`, ever, under any circumstance.
> - The `nbsnap export` command is the only sanctioned way to touch the
>   source, it issues `GET` only.
> - The HTTP client must refuse non-GET requests when the configured
>   `base_url` matches `NB_SOURCE_URL`, this is a guard rail, not a
>   convention.
> - Test fixtures, seeders, and integration suites target the local
>   netbox-docker test stack on ports `8080`/`8081`, never the production
>   source.
> - Writes flow source -> snapshot file -> destination. The destination
>   (`NB_DESTINATION_URL`) is the only NetBox that ever receives writes
>   from this tool.
>
> Violating this is a production incident. When in doubt, do nothing
> against the source and ask the operator first.

> ## SCOPE: NETWORK MODEL ONLY, NOT USERS OR NETBOX CONFIGURATION
>
> The snapshot carries the **modelled network** only. It does **not**
> carry users, hostnames-as-identity, NetBox-instance configuration,
> integrations, or operator settings.
>
> **In scope** (network model, sync this):
>
> - DCIM: Sites, Locations, Racks, Manufacturers, Device Types,
>   Platforms, Device Roles, Devices, Interfaces, Cables, the
>   connection topology.
> - IPAM: VLAN Groups, VLANs, Aggregates, Prefixes, IP Ranges,
>   IP Addresses, IPAM Roles, the addressing model.
> - The Custom Fields and Tags **used by the above objects**, plus the
>   Choice Sets those custom fields reference.
>
> **Out of scope** (do **not** sync, even when present on the source):
>
> - Users, Groups, ObjectPermissions, API Tokens, password material,
>   SSO/SAML configuration.
> - Tenancy (Tenants, Tenant Groups, Contacts, Contact Roles,
>   Contact Assignments). Organisational ownership, not network shape.
> - Webhooks, Data Sources, Custom Links, Saved Filters,
>   Notification Groups. NetBox-instance integrations.
> - Config Contexts, Config Templates, Scripts, Reports. NetBox-internal
>   operator tooling.
> - Journal entries, Object Change log, Image Attachments,
>   Bookmarks. Operational history and per-user state.
> - The source NetBox's own hostname/URL identity, even when modelled
>   as an `IPAddress.dns_name` on the source. The destination has its
>   own identity.
> - Any field whose meaning depends on the source's network locality
>   (RFC1918 receiver URLs, `.internal` DNS names, the per-instance
>   `SECRET_KEY`).
>
> This narrows the earlier "Layer 2" set in `goals.md`. Network model
> only is the authoritative scope, the earlier wider scope is
> **superseded** by this banner. Update derived documents
> (`goals.md`, `docs/01-scope.md`, `docs/02-data-model-scope.md`,
> related `FEAT-` tickets in `TODO.md`) to match before any
> implementation work touches those areas.

This project produces a **portable, machine-readable abstraction of a running
NetBox instance** and re-imports it into another instance. The goal is to move
the *modelled network* (devices, interfaces, IPs, VLANs, prefixes, cables,
custom fields, users, …) between NetBox installations that live on isolated
networks, without requiring shared database access or `pg_dump`/`psql`.

The intended workflow is:

```
NetBox A  ──[export]──►  snapshot/  ──[import]──►  NetBox B
```

The snapshot must be:

* **Self-contained.** Every foreign key is resolved to an object the import
  side can recreate locally, no parent IDs from source DB are reused.
* **Dependency-ordered.** Import runs in a sequence that respects NetBox's
  relational graph so constraint violations cannot happen.
* **Reproducible.** A re-export from a freshly imported NetBox produces an
  equivalent snapshot (modulo the explicitly excluded install-local fields).
* **Renderer-complete.** At minimum, every object the three renderers in
  `__reference/nb2kea/scripts/` (`netbox2cisco.py`, `netbox2junos.py`,
  `netbox2kea.py`) read must round-trip cleanly.

## Why this is hard (and why no public tool already does it)

NetBox is a relational PostgreSQL application with deep foreign-key chains
and many-to-many associations. The four obstacles, in summary:

1. **Schema complexity.** Child objects need parent IDs at create time, and
   parent IDs only exist after the parent is created in the destination DB.
2. **Format asymmetry.** NetBox's UI CSV export uses display names; CSV import
   wants primary keys. The two are not symmetric and cannot round-trip.
3. **Circular references.** `Device ↔ Interface ↔ IPAddress.primary_ip4 ↔
   Device` and `Cable ↔ Interface (both ends)` are real cycles. A flat
   sequential importer fails on them without a planner.
4. **Operational state.** Content types, permissions, plugin tables, change
   logs etc. are mixed in with primary data and don't survive naïve copy.

NetBox's own supported migration is `pg_dump` / `psql` plus the upstream
upgrade scripts. That is **not** an option here — the source and destination
NetBox installs sit in **separate networks** with no shared DB access. The
only stable contact surface is the **REST/GraphQL API**.

## How this project approaches it

* **Export by traversal, not by table.** Walk the API in dependency order and
  emit one object per record, keyed by a stable natural key (slug, name,
  composite tuple), not by the source DB primary key.
* **Snapshot as a graph, not a CSV.** The on-disk format is a versioned
  directory of JSON/YAML files per object type, with foreign keys expressed
  as natural-key references. Cycles (notably `Device.primary_ip4`) are
  resolved by a two-phase import (create, then patch the cycle-closing
  fields).
* **Plan, then apply.** The import side first compiles a topologically
  sorted execution plan, then applies it with idempotent upserts so a
  partially-applied snapshot is safe to resume.
* **Explicit scope.** Install-local fields (the NetBox instance's own IP,
  API tokens, secret keys, webhook receiver URLs that point at local
  services, etc.) are out of scope and *excluded* on export. See `goals.md`.

## Minimum viable data set

Derived from what the renderers in `__reference/nb2kea/scripts/` actually
read (see `__reference/nb2kea/reference_documentation/architecture_notes/11-netbox-data-requirements.md`
for the renderer-side authoritative list):

| Concern | NetBox endpoint(s) used by renderers |
| :--- | :--- |
| Sites | `dcim/sites/` (hall) |
| Locations | `dcim/locations/` (district / area) |
| Racks | `dcim/racks/` (+ `custom_fields.switch_count`) |
| Device Roles | `dcim/device-roles/` (`distribution_switches`, `access_switch`, `core_router`, …) |
| Device Types & Manufacturers | `dcim/device-types/`, `dcim/manufacturers/` |
| Devices | `dcim/devices/` (+ `custom_fields.district_token`, `primary_ip4`) |
| Interfaces | `dcim/interfaces/` (incl. `untagged_vlan`, `description`, `cable`) |
| Cables | `dcim/cables/` (access ⇄ dist mapping) |
| VLANs | `ipam/vlans/` (site-scoped + global, names → Junos identifiers) |
| Prefixes | `ipam/prefixes/` (+ `role` = `kea-bootstrap` / `kea-crew` / …) |
| IP Ranges | `ipam/ip-ranges/` (+ `role` = `kea-participant` / `kea-dist-mgmt` / …) |
| IP Addresses | `ipam/ip-addresses/` (assigned to interfaces, `dns_name`-tagged services) |
| Roles (Prefix/IP) | `ipam/roles/` (the `kea-*` slugs) |
| Custom Fields | `extras/custom-fields/` (`district_token`, `switch_count`) |
| Tags | `extras/tags/` |

Beyond this *renderer-minimum*, the broader scope adds network-model
objects only, per the "Network model only" banner above:

* **Wireless, Circuits, VPN** if the source instance models any of
  these as part of the physical or logical network.
* **NetBox version** of the source, recorded in the snapshot manifest so
  imports can refuse a mismatched destination version.

The previously listed Users, Groups, ObjectPermissions, Tenancy, Config
Contexts, Config Templates, Custom Links, Saved Filters, Webhooks, and
Data Sources are **no longer in scope** per the banner. Treat
`docs/02-data-model-scope.md` Layer 2 entries that touch identity,
tenancy, or NetBox-instance integration as out of scope until the docs
are updated to match.

See `docs/02-data-model-scope.md` for the full inventory and inclusion
rules, **pending an update pass** to align with the network-only
constraint.

## Reference material

* `__reference/nb2kea/` — the prior project. **Reuse its lessons, do not vendor
  it in.** Particularly useful:
  * `CLAUDE.md` — overall network architecture and renderer contract.
  * `reference_documentation/architecture_notes/11-netbox-data-requirements.md`
    — the renderer-side data contract (what the renderers must find in NetBox).
  * `reference_documentation/architecture_notes/07-naming-and-netbox-mapping.md`
    — field repurposing (Site = hall, Location = district, etc.).
  * `reference_documentation/netbox/` — mirrored NetBox v4.6.2 docs.
  * `scripts/netbox_utils/netbox_common.py` — a working `curl`-based NetBox
    client, useful as a reference for retry/backoff and pagination handling.
* `__reference/nb2kea/reference_documentation/netbox/integrations/rest-api.md`
  — primary API contract reference (mirrored locally).

## Operating conventions for this project

* **No third-party DB access.** Everything goes through the NetBox REST/GraphQL
  API. Even the official `pg_dump` route is explicitly *not* what we are
  building.
* **NetBox is the source of truth.** The snapshot is a *derived artefact* of
  the source NetBox; never edit it by hand to push data into the destination
  — round-trip through the source NetBox first.
* **Natural keys, not DB ids.** Every foreign key in the snapshot is a tuple
  the import side can resolve against the destination NetBox. Source-side
  numeric ids appear nowhere in the on-disk format.
* **Idempotent imports.** Re-running the importer on the same snapshot
  against the same destination is a no-op. This is how we recover from a
  partial run.
* **Excluded by design.** Install-local fields (NetBox URL, TLS material,
  API tokens, webhook receiver URLs, the host's primary IP if it is the
  NetBox box itself, change-log history) are not part of the snapshot.
  See `goals.md` for the precise exclusion list.
* **TODO.md hygiene, deletion on completion.** When a ticket's
  implementation is committed to the repo, its block in
  `TODO.md` must be removed in the same change or in a tightly
  scoped follow-up commit. Git history is the authoritative
  record of what shipped; leaving completed tickets in the
  open backlog inflates the file, masks the actual remaining
  work, and risks drift between the ticket text and the code
  that delivers it. The commit message references the ticket id
  so `git log --all --grep="FEAT-XX"` finds the implementation.
  An audit pass that removes a batch of tickets together is
  acceptable and preferred when several related tickets land
  in the same session.

## Repository layout (planned)

```
/workspace
├── CLAUDE.md                  (this file)
├── goals.md                   project goals, in/out of scope
├── docs/                      design + reference docs
│   ├── INDEX.md
│   ├── 01-scope.md
│   ├── 02-data-model-scope.md
│   ├── 03-dependency-graph.md
│   ├── 04-snapshot-format.md
│   └── 05-export-import-workflow.md
├── __reference/nb2kea/        existing renderer project, READ-ONLY reference
└── (src/, tests/ to be created when implementation starts)
```

Implementation code, snapshot examples, and tests will land in
`src/`, `examples/`, and `tests/` once the design docs are stable. The
**first deliverable is the design**, not the script.

## Environment & endpoints

Two NetBox instances are configured for this project. Credentials live
in `/workspace/.env` (never commit — see `.gitignore`). The URLs are
also surfaced via `.claude/settings.json` so future agent sessions know
about them; tokens stay in `.env` only.

| Role | URL env var | Token env var | Default value (URL) |
| :--- | :--- | :--- | :--- |
| Source (**PRODUCTION, READ-ONLY, do not write**) | `NB_SOURCE_URL` | `NB_SOURCE_TOKEN` | `https://host.docker.internal:8443` (forwarded production NetBox, self-signed TLS, GET only) |
| Destination | `NB_DESTINATION_URL` | `NB_DESTINATION_TOKEN` | `https://netbox.i.louie.se` |

See the read-only banner at the top of this file. Any non-GET request
against `NB_SOURCE_URL` is a production incident.

The `nbsnap` CLI is **one endpoint per invocation**; the convention is
to call:

```bash
nbsnap export --url "$NB_SOURCE_URL"      --token "$NB_SOURCE_TOKEN"      --out ./snapshot/
nbsnap import --url "$NB_DESTINATION_URL" --token "$NB_DESTINATION_TOKEN" --in  ./snapshot/
```

The HTTP client also honours the legacy `NB_URL` / `NB_TOKEN`
single-endpoint names used by `__reference/nb2kea/` so existing scripts
keep working.

TLS verification policy: **on by default**; the local
`host.docker.internal:8443` endpoint uses a self-signed cert and
requires `--no-verify-tls`. The public destination keeps verification on.

## Implementation guidelines (carried over from nb2kea)

* **Source is read-only.** The HTTP client must implement a hard
  guard rail: when `base_url` matches `NB_SOURCE_URL`, only `GET`
  requests are dispatched, every other verb raises before the
  socket is opened. See the banner at the top of this file.
* **API tokens** live in environment variables, never in code or the
  snapshot. The project uses the four-variable scheme above; the older
  `NB_TOKEN` is accepted as a fallback.
* **TLS verification** posture is configurable per-instance; the production
  NetBox in the reference project uses a self-signed cert and a host
  resolve pin.
* **Retry/backoff** for transient HTTP failures is mandatory — copy the
  pattern from `__reference/nb2kea/scripts/netbox_utils/netbox_common.py`
  (curl exit 28, HTTP 429, HTTP 5xx).
* **Pagination.** NetBox honours `?limit=0` on most list endpoints; still
  follow `next` defensively.
* **No silent defaults.** When a required NetBox object is missing on
  export or import, fail loudly with the object kind and natural key, the
  same posture the renderers already take.
