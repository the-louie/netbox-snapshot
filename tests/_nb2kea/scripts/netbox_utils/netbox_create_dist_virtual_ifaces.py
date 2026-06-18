#!/usr/bin/env python3
"""
Create virtual interfaces (lo0.0, irb.*) on every distribution switch in NetBox.

For each device with role 'distribution_switches', the script creates,
  lo0.0                              loopback
  irb.600                            per district VLAN 600 mgmt SVI
  irb.1100, irb.1101                 OSPF linknets to MX-01 (default / internet)
  irb.1200, irb.1201                 OSPF linknets to MX-02 (default / internet)
  irb.<vlan>                         one per participant table the dist serves

Idempotent. Interfaces already present on a device are skipped, so the
script can be re run after fixing failures without making duplicates.

The participant VLAN list for each dist is derived from NetBox, the dist's
Location is read, the racks inside it are walked through `dist_tables_for`,
and each (table, switch_count) tuple is turned into a VLAN id via the
hall's `HALL_VLAN_BASE`. No hard coded plan is carried in this script.

Usage,
    export NB_TOKEN="..."
    ./netbox_create_dist_virtual_ifaces.py                    # dry run
    ./netbox_create_dist_virtual_ifaces.py --apply
    ./netbox_create_dist_virtual_ifaces.py --dist D-THE-FORGE-SW --apply
"""

from __future__ import annotations

import argparse
import sys

from netbox_common import (
    NetboxClient,
    ROLE_DIST,
    dist_tables_for,
    participant_vlan_for_table,
    require_token,
    vlan_to_irb_description,
)

MGMT_VLAN = 600
OSPF_VLANS = {
    1100: "GLITCHED-MX-01_OSPF_DEFAULT",
    1101: "GLITCHED-MX-01_OSPF_INTERNET",
    1200: "GLITCHED-MX-02_OSPF_DEFAULT",
    1201: "GLITCHED-MX-02_OSPF_INTERNET",
}


def participant_vids_for(client: NetboxClient, dist: dict) -> list[int]:
    """
    Walk the dist's tables and return the VLAN id for each. The hall
    letter is the first character of the dist's name, which feeds into
    `participant_vlan_for_table` with the table number from `dist_tables_for`.
    """
    name = dist["name"]
    hall = name[0]
    try:
        tables = dist_tables_for(client, name)
    except RuntimeError as exc:
        # A dist with no tables in NetBox still gets the baseline
        # interfaces, the caller can detect the empty list and warn.
        print(f"  [warn] {name}, {exc}", file=sys.stderr)
        return []
    return [participant_vlan_for_table(hall, table_num) for table_num, _ in tables]


def desired_interfaces(participant_vids: list[int]
                       ) -> list[tuple[str, int | None, str]]:
    """Return list of (interface_name, vlan_vid_or_None, description)."""
    desired: list[tuple[str, int | None, str]] = []
    desired.append(("lo0.0", None, "Loopback"))
    desired.append(("irb.600", MGMT_VLAN, "ACCESS-MGMT"))
    for vid, label in OSPF_VLANS.items():
        desired.append((f"irb.{vid}", vid, label))
    for vid in participant_vids:
        desired.append((f"irb.{vid}", vid, vlan_to_irb_description(vid)))
    return desired


def process_dist(client: NetboxClient, device: dict,
                 vlan_vid_to_id: dict[int, int],
                 apply: bool) -> tuple[int, int, int]:
    """Return (created, skipped_existing, failed)."""
    name = device["name"]
    location_name = (device.get("location") or {}).get("name", "?")
    print(f"\n=== {name}  (id={device['id']}, location={location_name}) ===")

    existing = client.get_all(f"dcim/interfaces/?device_id={device['id']}")
    existing_names = {i["name"] for i in existing}

    participant_vids = participant_vids_for(client, device)
    if not participant_vids:
        print(f"  [warn] no participant VLANs derived for {name!r}, "
              f"only baseline interfaces (lo0, irb.600, OSPF IRBs) will be "
              f"created. Check the dist's Location and the switch_count "
              f"custom field on each rack inside it.")

    desired = desired_interfaces(participant_vids)
    created = skipped = failed = 0

    for iface_name, vid, desc in desired:
        if iface_name in existing_names:
            print(f"  [ok]   {iface_name:<14} exists  (description ignored)")
            skipped += 1
            continue

        body: dict = {
            "device": device["id"],
            "name": iface_name,
            "type": "virtual",
            "enabled": True,
            "description": desc,
        }
        if vid is not None:
            if vid not in vlan_vid_to_id:
                print(f"  [SKIP] {iface_name:<14} VLAN {vid} not found in IPAM")
                failed += 1
                continue
            body["mode"] = "access"
            body["untagged_vlan"] = vlan_vid_to_id[vid]

        if not apply:
            print(f"  [DRY]  {iface_name:<14} would create  vid={vid}  desc='{desc}'")
            created += 1
            continue

        try:
            client.post("dcim/interfaces/", body)
            print(f"  [NEW]  {iface_name:<14} created       vid={vid}  desc='{desc}'")
            created += 1
        except RuntimeError as exc:
            print(f"  [FAIL] {iface_name:<14} {exc}")
            failed += 1

    return created, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually POST interface creates. Default is dry run.")
    parser.add_argument("--dist", metavar="NAME",
                        help="Only process this dist by NetBox name.")
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()
    print(f"Mode, {'APPLY' if args.apply else 'DRY-RUN'}")

    devices = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
    if args.dist:
        devices = [d for d in devices if d["name"] == args.dist]
        if not devices:
            print(f"Error, no device named {args.dist!r} with role {ROLE_DIST}",
                  file=sys.stderr)
            return 1
    print(f"Found {len(devices)} dist device(s)")

    vlans = client.get_all("ipam/vlans/")
    vlan_vid_to_id = {v["vid"]: v["id"] for v in vlans}
    print(f"Loaded {len(vlan_vid_to_id)} VLANs from IPAM")

    totals = [0, 0, 0]
    for device in sorted(devices, key=lambda d: d["name"]):
        try:
            c, s, f = process_dist(client, device, vlan_vid_to_id, args.apply)
            totals[0] += c
            totals[1] += s
            totals[2] += f
        except RuntimeError as exc:
            print(f"\n[FATAL] {device['name']}, {exc}", file=sys.stderr)
            totals[2] += 1

    print()
    print("=" * 60)
    print(f"  {'Created' if args.apply else 'Would create'}, {totals[0]}")
    print(f"  Skipped (already exists),                {totals[1]}")
    print(f"  Failed or not found,                     {totals[2]}")
    print("=" * 60)

    if not args.apply:
        print("\nDry run complete. Re run with --apply to actually create.")

    return 0 if totals[2] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
