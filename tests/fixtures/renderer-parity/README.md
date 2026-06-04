# renderer-parity fixture set

Hand-built synthetic network for the TEST-08 acceptance gate. The
seven JSON files seed a netbox-docker source instance with a
deterministic shape so a snapshot export, an import to a clean
destination, and a render of both sides through nb2kea can be
diffed byte-for-byte (modulo the `NETBOX_HOST` banner line).

Files are numbered so a simple
`for f in 0*.json; do seed_one $f; done` shell respects the
creation order. Foreign keys reference natural keys from earlier
files only.

Shape (see TEST-08a1/a2/a3 in TODO.md for the rationale):

- `01-sites.json` — `hall-d` (`name = "Hall D"`).
- `02-locations.json` — `the-forge`, `mirage-palace` in `hall-d`.
- `03-racks.json` — `D39`, `D40` in the-forge; `D55`, `D56` in
  mirage-palace.
- `04-manufacturers.json` — `cisco`, `juniper`.
- `05-device-types.json` — `cisco/ws-c2950t-24`,
  `juniper/ex4100-24t`.
- `06-device-roles.json` — `access_switch`,
  `distribution_switches`.
- `07-devices.json` — `D39A` through `D56B` (access), plus the
  single `D-THE-FORGE-SW` dist.
- `08-vlans.json` — `vlan-600` (vid 600, MGMT).
- `09-prefixes.json` — `172.16.1.0/24`, role `kea-dist-mgmt`.
- `10-interfaces.json` — per-device Vlan600 SVI, Gi0/2 uplink on
  each access switch, `ge-0/0/0..7` plus `irb.600` on the dist.
- `11-ip-addresses.json` — `172.16.1.10..17/24` on the access
  Vlan600 SVIs, `172.16.1.1/24` on `irb.600`.
- `12-cables.json` — `D??[AB]:Gi0/2` to
  `D-THE-FORGE-SW:ge-0/0/N` (N=0..7).

The actual JSON files are intentionally NOT committed in this
loop's batch close — the seeding script reads them and lands the
records on a netbox-docker stack via REST. The shapes above are
the contract; TEST-08a1/a2/a3 land them as a focused pass once
the lab is reachable.
