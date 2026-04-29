# Integration test fixtures

This directory holds the two-instance NetBox test stack that the
integration suite spins up, plus the seed JSON files that pre-load
each instance with a known starting state.

## DO NOT CONFUSE WITH PRODUCTION

The two stacks here run on host ports **8080** (source) and **8081**
(destination). They are **not** the operator's production NetBox at
`host.docker.internal:8443`. The production banner in
[`CLAUDE.md`](../../CLAUDE.md) applies here too: never point the
seeder or any integration test at the production URL.

## NetBox image tag

Pinned in the top-level `Makefile` as `NETBOX_DOCKER_TAG`. The
current value is `v4.6-3.4.1` (netbox-docker tag for
NetBox 4.6.x). Bumping the tag is a dedicated PR that re-runs the
integration suite and updates the date below.

| Field | Value |
| :--- | :--- |
| Pinned tag | `v4.6-3.4.1` |
| NetBox version it carries | 4.6.x |
| Date pinned | 2026-06-14 |

## Layout

```
fixtures/
├── README.md                    this file
├── source/
│   ├── docker-compose.yml       host port 8080, volumes -source
│   └── env/netbox.env           SECRET_KEY + admin token A
├── dest/
│   ├── docker-compose.yml       host port 8081, volumes -dest
│   └── env/netbox.env           SECRET_KEY + admin token B (distinct)
├── seed.py                      idempotent seeder, hits one stack at a time
└── seed/
    ├── 00-roles.json            DeviceRole, INFRA-03d
    ├── 01-sites.json            Sites, INFRA-03e
    ├── 02-locations.json
    ├── 03-manufacturers.json    INFRA-03f
    ├── 04-device-types.json
    ├── 05-devices.json
    ├── 06-interfaces.json
    ├── 07-vlans.json            INFRA-03g1
    ├── 08-prefixes.json
    ├── 09-ip-ranges.json
    ├── 10-ip-addresses.json
    ├── 11-cables.json           INFRA-03h
    └── 12-device-primary-ips.json  INFRA-03g2, _resolve patch step
```

## Dependencies between seed files

The seeder runs files in lexical order. The numbering encodes the
dependency chain:

* `00-roles.json` lands first because devices reference roles.
* `01-sites.json` and `02-locations.json` land before any device
  fixture because devices reference sites and locations.
* `03-manufacturers.json` and `04-device-types.json` land before
  devices because devices reference device types.
* `05-devices.json` lands before `06-interfaces.json` (interfaces
  reference devices).
* `07-vlans.json` through `10-ip-addresses.json` land the
  addressing model. Interfaces from `06` are looked up by
  `(device.name, name)` when assigning IPs.
* `11-cables.json` connects two devices' interfaces, depends on
  `06-interfaces.json`.
* `12-device-primary-ips.json` patches Devices to set
  `primary_ip4`, depends on `10-ip-addresses.json`.

## SECRET_KEY drift between stacks

Source and destination use **different** `SECRET_KEY` values. This
is on purpose, the password-hash portability friction
([`docs/frictions/07`](../../docs/frictions/07-auth-and-secret-portability.md))
is exercised on every round-trip integration run because of this.

## Running locally

The top-level Makefile wraps the lifecycle:

```bash
make stack-up        # bring both stacks up in detached mode
make stack-wait      # poll /api/status/ on both, fail after 90s
make stack-seed      # apply all seed/*.json files in order
make stack-status    # docker compose ps for both
make stack-down      # tear both down with -v
```

The seeder is idempotent. Re-running `make stack-seed` against a
stack that already has the fixtures applied is a no-op and prints
`NOOP` for each row.
