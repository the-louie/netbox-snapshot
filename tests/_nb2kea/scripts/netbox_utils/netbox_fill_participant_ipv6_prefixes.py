#!/usr/bin/env python3
"""
Populate the IPv6 `/64` participant Prefixes in NetBox IPAM.

Each participant VLAN owns one `2a02:1420:1c0:<vid>::/64` prefix. The
renderer used to synthesise this address at render time, the strict-fail
migration replaced that with a NetBox IPAddress lookup, but the parent
Prefix object is still missing from IPAM. An operator searching NetBox
for `2a02:1420:1c0:239::1` finds the IPAddress, the parent Prefix is
absent, the IPAM tree therefore lies about the address space.

This is a one shot, idempotent change. The script,

  1. Reads every VLAN object from NetBox.
  2. Filters to the participant VIDs that map to a hall in
     `HALL_VLAN_BASE` (today, halls C and D, every hall added later
     gets the same treatment automatically once `HALL_VLAN_BASE`
     names it).
  3. Computes the `/64` prefix `2a02:1420:1c0:<vid>::/64` per VLAN.
  4. Either finds an existing Prefix matching that CIDR or POSTs a new
     one. Either way the VLAN linkage and the description are made to
     match. The description copies the NetBox `VLAN.name`, so an
     operator browsing IPAM sees the same human label they see in the
     VLANs table.
  5. Reports counts at the end.

The format `2a02:1420:1c0:<vid>::/64` matches the historical Glitched
convention, the decimal VID is dropped into the IPv6 hex group
position verbatim. The renderer reads the per interface IPAddress, not
this Prefix, the Prefix exists so the IPAM tree is complete.

Usage,
    export NB_TOKEN="..."
    ./netbox_fill_participant_ipv6_prefixes.py            # dry run (default)
    ./netbox_fill_participant_ipv6_prefixes.py --apply    # actually create or patch
"""

from __future__ import annotations

import argparse
import sys

from netbox_common import (
    HALL_VLAN_BASE,
    NetboxClient,
    require_token,
)

# The prefix template the rest of the toolchain has settled on. The vid
# value drops into the fourth hextet position as a decimal string, the
# Junos and Kea sides interpret the resulting IPv6 token the same way,
# so the bootstrap is byte for byte aligned with the rendered config.
IPV6_PARTICIPANT_FMT = "2a02:1420:1c0:{vid}::/64"


def is_participant_vid(vid: int) -> bool:
    """
    Mirror of `vlan_to_irb_description`'s membership test. A participant
    VLAN sits in the open interval `(base, base + 56]` for some hall
    base in `HALL_VLAN_BASE`. The base itself (e.g. 100 for hall C) is
    not a table, it is the hall marker and stays out of IPAM.
    """
    for base in HALL_VLAN_BASE.values():
        if base < vid <= base + 56:
            return True
    return False


def find_prefix(client: NetboxClient, cidr: str) -> dict | None:
    """Return the Prefix object that matches this exact CIDR or None."""
    matches = client.get_all(f"ipam/prefixes/?prefix={cidr}")
    return matches[0] if matches else None


def ensure_prefix(client: NetboxClient, vlan: dict,
                  apply: bool) -> str:
    """
    Ensure the IPv6 /64 prefix for this participant VLAN exists and
    carries the right VLAN linkage and description. Returns a status
    string the caller summarises.

    Three terminal states,
      `exists`     prefix and metadata already correct, nothing to do.
      `created`    new Prefix POSTed.
      `patched`    Prefix existed but VLAN or description drifted.
                   Description and VLAN linkage are updated in place.

    Two dry run states mirror the apply paths,
      `dry_create` and `dry_patch`.
    """
    cidr = IPV6_PARTICIPANT_FMT.format(vid=vlan["vid"])
    target_desc = vlan.get("name") or ""
    target_vlan_id = vlan["id"]

    existing = find_prefix(client, cidr)
    if existing:
        current_vlan = (existing.get("vlan") or {}).get("id")
        current_desc = existing.get("description") or ""
        if current_vlan == target_vlan_id and current_desc == target_desc:
            return "exists"
        if not apply:
            return "dry_patch"
        client.patch(f"ipam/prefixes/{existing['id']}/", {
            "vlan": target_vlan_id,
            "description": target_desc,
        })
        return "patched"

    if not apply:
        return "dry_create"
    client.post("ipam/prefixes/", {
        "prefix":      cidr,
        "vlan":        target_vlan_id,
        "description": target_desc,
        "status":      "active",
    })
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually POST or PATCH. Default is dry run.")
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()

    print("Fetching VLANs from NetBox ...")
    vlans = client.get_all("ipam/vlans/")
    participants = [v for v in vlans if is_participant_vid(v.get("vid") or 0)]
    print(f"  total VLANs,        {len(vlans)}")
    print(f"  participant VLANs,  {len(participants)}")

    if not participants:
        print("Nothing to do, no participant VLANs in NetBox.")
        return 0

    # Sort by vid so the run output reads in the same order as a NetBox
    # IPAM browse, makes spot checking the dry run easier.
    counts: dict[str, int] = {}
    for vlan in sorted(participants, key=lambda v: v["vid"]):
        status = ensure_prefix(client, vlan, args.apply)
        counts[status] = counts.get(status, 0) + 1
        vid = vlan["vid"]
        cidr = IPV6_PARTICIPANT_FMT.format(vid=vid)
        name = vlan.get("name") or "<unnamed>"
        print(f"  [{status:<10}] vid {vid:<4} {cidr}  ({name})")

    print()
    print("=" * 60)
    for status, n in sorted(counts.items()):
        print(f"  {status:<12} {n}")
    print("=" * 60)

    if not args.apply:
        print("\nDry run complete. Re run with --apply to commit the changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
