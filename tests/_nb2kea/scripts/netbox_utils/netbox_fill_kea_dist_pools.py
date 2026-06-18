#!/usr/bin/env python3
"""
Ensure NetBox carries a `kea-dist-mgmt` IPAM role and one IP Range per
dist mgmt /24 with that role.

The Kea renderer (`netbox2kea.py`) queries
`ipam/ip-ranges/?role=kea-dist-mgmt` per dist to find the DHCP pool
that lives inside that dist's mgmt /24. The API driven NetBox
bootstrap created the prefixes themselves but never the role and
never the per dist pool ranges, so the Kea renderer aborts at fleet
discovery. This script closes the gap.

What it touches and what it does not,

  Stage 1, role,
    * If `kea-dist-mgmt` role exists, `[ok-role]`.
    * Else POST a new role with that slug, `[created-role]`.

  Stage 2, ranges, for each device with role `distribution_switches`,
    * Find the irb.600 IPv4 address, derive the /24 from it.
    * Compute the conventional pool, `<base>.100` to `<base>.254`,
      per `reference_documentation/howto/add-a-dist.md` section 4.
    * If a range with role `kea-dist-mgmt` already exists inside
      that /24 and the bounds match, `[ok]`.
    * If a range with role `kea-dist-mgmt` exists with different
      bounds, `[skip-custom-bounds]`, the operator chose a
      non default pool and the script leaves it alone.
    * If a range without role `kea-dist-mgmt` exists inside the /24,
      `[CONFLICT]`, refuse to overlap operator state.
    * Otherwise POST a new range with the proposed bounds.

Default dry run, `--apply` commits, `--dist NAME` limits to one dist.
Exit code 2 on any `[CONFLICT]` or `[FAIL]`, 0 otherwise. Matches
the convention of the other `netbox_fill_*` bootstrap scripts.

Usage,
    export NB_TOKEN="..."
    ./netbox_fill_kea_dist_pools.py                 # dry run, all dists
    ./netbox_fill_kea_dist_pools.py --apply         # commit changes
    ./netbox_fill_kea_dist_pools.py --dist D-THE-FORGE-SW --apply
"""

from __future__ import annotations

import argparse
import ipaddress
import sys

from netbox_common import (
    NetboxClient,
    ROLE_DIST,
    require_token,
)

ROLE_SLUG = "kea-dist-mgmt"
ROLE_NAME = "Kea Dist Mgmt"
ROLE_DESCRIPTION = (
    "DHCP pool inside each dist's mgmt /24 (VLAN 600), serves access "
    "switch management IPs via Option 82 circuit-id reservations."
)

# Conventional pool boundaries inside the dist mgmt /24. The first 99
# host addresses (.1 through .99) are reserved for the dist gateway
# (.1) and access switch reservations (the rest), the pool covers
# .100 through .254. The /24 broadcast .255 is excluded. See
# `reference_documentation/howto/add-a-dist.md` section 4.
POOL_FIRST_OFFSET = 100
POOL_LAST_OFFSET = 254


def ensure_role(client: NetboxClient, apply: bool
                ) -> tuple[int | None, str]:
    """
    Find or create the kea-dist-mgmt role. Returns (role_id, status).
    role_id is None on dry run when the role does not exist yet.

    When the role exists but its name or description has drifted from
    the script's canonical values the return status carries the drift
    detail so the operator notices. Drift is informational, the script
    does not patch existing role metadata, the slug is the only field
    the renderer actually keys on.
    """
    existing = client.get_all(f"ipam/roles/?slug={ROLE_SLUG}")
    if existing:
        role = existing[0]
        drift = []
        if role.get("name") != ROLE_NAME:
            drift.append(f"name={role.get('name')!r}")
        if (role.get("description") or "") != ROLE_DESCRIPTION:
            drift.append("description differs")
        if drift:
            return role["id"], f"[ok-role-with-drift] {', '.join(drift)}"
        return role["id"], "[ok-role]"
    if not apply:
        return None, "[dry_create-role]"
    created = client.post("ipam/roles/", {
        "name": ROLE_NAME,
        "slug": ROLE_SLUG,
        "description": ROLE_DESCRIPTION,
    })
    return created["id"], "[created-role]"


def irb600_v4(client: NetboxClient, dist: dict) -> str | None:
    """
    Resolve the dist's irb.600 IPv4 address as `<addr>/<prefix>`, or
    None if missing. The renderer's case insensitive helper is in
    netbox2junos.py and not shared, here the exact name is enough.
    """
    ifs = client.get_all(
        f"dcim/interfaces/?device_id={dist['id']}&name=irb.600"
    )
    if not ifs:
        return None
    ips = client.get_all(
        f"ipam/ip-addresses/?interface_id={ifs[0]['id']}"
    )
    for ip in ips:
        if ipaddress.ip_interface(ip["address"]).version == 4:
            return ip["address"]
    return None


def process_dist(client: NetboxClient, dist: dict, role_id: int | None,
                 all_ranges: list[dict], apply: bool) -> str:
    """
    Resolve the proposed pool for one dist and either POST or report
    what would change. Returns the status tag.
    """
    addr_str = irb600_v4(client, dist)
    if addr_str is None:
        return "[skip-no-irb600]"

    iface = ipaddress.ip_interface(addr_str)
    net = iface.network
    if net.prefixlen != 24:
        return f"[skip-not-/24] irb.600 is {net}"

    pool_start = net.network_address + POOL_FIRST_OFFSET
    pool_end = net.network_address + POOL_LAST_OFFSET
    start_cidr = f"{pool_start}/{net.prefixlen}"
    end_cidr = f"{pool_end}/{net.prefixlen}"

    # Find every existing range that overlaps this /24, including any
    # whose start sits below the /24 but whose end is inside (or vice
    # versa). Filtering on start alone would miss a range straddling
    # the boundary and the script would then POST an overlapping pool.
    in_subnet = []
    for r in all_ranges:
        try:
            rs = ipaddress.ip_interface(r["start_address"]).ip
            re_ = ipaddress.ip_interface(r["end_address"]).ip
        except (ValueError, KeyError):
            continue
        if rs in net or re_ in net:
            in_subnet.append(r)

    matching = [
        r for r in in_subnet
        if (r.get("role") or {}).get("slug") == ROLE_SLUG
    ]
    if matching:
        # Bounds match check, the renderer is happy with any kea-dist-mgmt
        # range that lives inside the /24, but we only auto-confirm the
        # conventional bounds.
        for r in matching:
            rs = ipaddress.ip_interface(r["start_address"]).ip
            re_ = ipaddress.ip_interface(r["end_address"]).ip
            if rs == pool_start and re_ == pool_end:
                return f"[ok] {pool_start} - {pool_end}"
        bounds = ", ".join(
            f"{r['start_address']}-{r['end_address']}" for r in matching
        )
        return f"[skip-custom-bounds] existing kea-dist-mgmt range, {bounds}"

    # No matching role range, refuse to overlap any other range in the
    # same /24. The operator may have a non Kea range parked here. List
    # every overlapping range so the operator can resolve all of them in
    # one pass rather than discovering them across multiple dry runs.
    if in_subnet:
        details = ", ".join(
            f"{r['start_address']}-{r['end_address']} "
            f"(role={((r.get('role') or {}).get('slug')) or 'none'})"
            for r in in_subnet
        )
        return f"[CONFLICT-other-range] {details}"

    if not apply:
        return f"[dry_create] {start_cidr} - {end_cidr}"

    if role_id is None:
        # Defensive, role does not exist and apply is true, the caller
        # should have ensured the role first.
        return "[FAIL-no-role]"

    client.post("ipam/ip-ranges/", {
        "start_address": start_cidr,
        "end_address": end_cidr,
        "role": role_id,
        "status": "active",
        "description": f"{dist['name']} mgmt DHCP pool",
    })
    return f"[created] {start_cidr} - {end_cidr}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually POST. Default is dry run.")
    parser.add_argument("--dist", metavar="NAME",
                        help="Limit the run to a single dist by Device.name.")
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()

    # Stage 1, role.
    role_id, role_status = ensure_role(client, args.apply)
    print(f"role: {role_status} (slug={ROLE_SLUG!r}, id={role_id})")

    # Stage 2, ranges.
    dists = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
    if args.dist:
        dists = [d for d in dists if d["name"] == args.dist]
        if not dists:
            print(f"Error, dist {args.dist!r} not found.", file=sys.stderr)
            return 1

    # One bulk fetch of every IP Range, the per dist loop filters in
    # Python. NetBox has no clean "ranges inside this prefix" filter
    # so the Python pass keeps the logic explicit.
    all_ranges = client.get_all("ipam/ip-ranges/")

    print(f"\nFound {len(dists)} dist(s), {len(all_ranges)} existing IP Range(s)")

    counts: dict[str, int] = {}
    for dist in sorted(dists, key=lambda d: d["name"]):
        status = process_dist(client, dist, role_id, all_ranges, args.apply)
        bucket = status.split(" ", 1)[0]
        counts[bucket] = counts.get(bucket, 0) + 1
        print(f"  {dist['name']:25s} {status}")

    print()
    print("=" * 60)
    print(f"  role: {role_status}")
    for bucket in sorted(counts):
        print(f"  {bucket:<26} {counts[bucket]}")
    print("=" * 60)

    if not args.apply:
        print("\nDry run complete. Re run with --apply to commit the changes.")

    failed = any(
        b.startswith("[CONFLICT") or b.startswith("[FAIL") for b in counts
    )
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
