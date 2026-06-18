#!/usr/bin/env python3
"""
Assign mgmt IPs to the access switches.

For each access switch created by `netbox_create_access_switches.py`,
  1. Ensure a `Vlan600` virtual interface exists on the device (created
     with `untagged_vlan = 600` if missing).
  2. Assign an IPAddress to that Vlan600.
  3. Set `Device.primary_ip4` to point at it.

IP rule, per the dist's mgmt /24,
    octet = 10 + index_within_dist_in_ROWS_order

Index 0 is the first slot A access switch, 1 is its slot B sibling or the
next slot A if single switch, and so on. This matches the dist's ge-0/0/x
port order, so 'octet = 10 + ge port' is the operator facing mnemonic.

The per dist mgmt /24 and the table list are both read from NetBox through
the shared module, the script no longer carries a copy of either.

Idempotent, existing interfaces and IPs are left alone, re runs only fill
gaps.

Usage,
    export NB_TOKEN="..."
    ./netbox_assign_access_ips.py                            # dry run (default)
    ./netbox_assign_access_ips.py --apply
    ./netbox_assign_access_ips.py --dist D-THE-FORGE-SW --apply
"""

from __future__ import annotations

import argparse
import ipaddress
import sys

from netbox_common import (
    NetboxClient,
    ROLE_ACCESS,
    ROLE_DIST,
    dist_info_for,
    dist_tables_for,
    make_access_hostname,
    require_token,
)

MGMT_SVI_NAME = "Vlan600"
MGMT_VLAN_VID = 600
OCTET_BASE = 10  # first access switch in a dist gets .10


def ensure_svi(client: NetboxClient, device_id: int, vlan_id_for_600: int,
               existing_ifaces: dict[str, dict], apply: bool
               ) -> tuple[str, dict | None]:
    """Make sure a Vlan600 virtual interface exists on the device."""
    if MGMT_SVI_NAME in existing_ifaces:
        return "exists", existing_ifaces[MGMT_SVI_NAME]
    body = {
        "device": device_id,
        "name": MGMT_SVI_NAME,
        "type": "virtual",
        "enabled": True,
        "mode": "access",
        "untagged_vlan": vlan_id_for_600,
        "description": "Mgmt SVI (VLAN 600)",
    }
    if not apply:
        return "dry", body
    return "new", client.post("dcim/interfaces/", body)


def ensure_ip(client: NetboxClient, address: str, iface_id: int,
              description: str, existing_by_iface: dict[int, list[dict]],
              apply: bool) -> tuple[str, dict | None]:
    # Already on this interface keeps the run idempotent on retries.
    for ip in existing_by_iface.get(iface_id, []):
        if ip["address"] == address:
            return "exists", ip
    # The address might already exist in IPAM, possibly assigned, possibly
    # free, possibly on a different interface entirely.
    found = client.get_one(f"ipam/ip-addresses/?address={address}")
    matches = (found or {}).get("results", []) if isinstance(found, dict) else []
    if matches:
        for ip in matches:
            ao = ip.get("assigned_object")
            if ao and ao.get("id") == iface_id:
                return "exists", ip
        for ip in matches:
            if not ip.get("assigned_object"):
                if not apply:
                    return "dry_adopt", ip
                patched = client.patch(f"ipam/ip-addresses/{ip['id']}/", {
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": iface_id,
                    "description": description,
                })
                return "adopt", patched
        return "conflict", matches[0]

    body = {
        "address": address,
        "status": "active",
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": iface_id,
        "description": description,
    }
    if not apply:
        return "dry", body
    return "new", client.post("ipam/ip-addresses/", body)


def process_one(client: NetboxClient, device: dict, hostname: str,
                ip_addr: str, vlan_id_for_600: int, apply: bool
                ) -> tuple[int, int, int]:
    """Return (created, skipped, failed) for this one device."""
    created = skipped = failed = 0

    ifaces = client.get_all(f"dcim/interfaces/?device_id={device['id']}")
    by_name = {i["name"]: i for i in ifaces}

    try:
        svi_status, svi = ensure_svi(client, device["id"], vlan_id_for_600,
                                      by_name, apply)
    except RuntimeError as exc:
        print(f"  [FAIL] {hostname:<6} Vlan600 create, {exc}")
        return 0, 0, 1

    if svi_status == "exists":
        print(f"  [ok]   {hostname:<6} Vlan600 exists")
        skipped += 1
    elif svi_status == "dry":
        # The SVI does not yet exist, the script cannot continue with an
        # interface id, so preview the IP assignment on a separate line and
        # return early.
        print(f"  [DRY]  {hostname:<6} would create Vlan600")
        created += 1
        print(f"  [DRY]  {hostname:<6} would assign {ip_addr} to Vlan600")
        created += 1
        return created, skipped, failed
    else:
        print(f"  [NEW]  {hostname:<6} created Vlan600 (id={svi['id']})")
        created += 1

    existing_ips = client.get_all(f"ipam/ip-addresses/?device_id={device['id']}")
    existing_by_iface: dict[int, list[dict]] = {}
    for ip in existing_ips:
        ao = ip.get("assigned_object")
        if ao and ao.get("id"):
            existing_by_iface.setdefault(ao["id"], []).append(ip)

    try:
        ip_status, ip_obj = ensure_ip(client, ip_addr, svi["id"],
                                       f"{hostname} mgmt",
                                       existing_by_iface, apply)
    except RuntimeError as exc:
        print(f"  [FAIL] {hostname:<6} IP assign, {exc}")
        return created, skipped, failed + 1

    if ip_status == "exists":
        print(f"  [ok]   {hostname:<6} {ip_addr:<18} already assigned")
        skipped += 1
    elif ip_status == "adopt":
        print(f"  [ADP]  {hostname:<6} {ip_addr:<18} adopted (was unassigned)")
        created += 1
    elif ip_status == "dry_adopt":
        print(f"  [DRY]  {hostname:<6} would adopt existing {ip_addr}")
        created += 1
    elif ip_status == "conflict":
        other = (ip_obj or {}).get("assigned_object") or {}
        other_iface = other.get("name", "?")
        other_dev = (other.get("device") or {}).get("name", "?")
        print(f"  [CFLT] {hostname:<6} {ip_addr:<18} already on "
              f"{other_dev}/{other_iface}")
        return created, skipped, failed + 1
    elif ip_status == "dry":
        print(f"  [DRY]  {hostname:<6} would assign {ip_addr} to Vlan600")
        created += 1
        # primary_ip4 PATCH is skipped during dry runs, the IP object id
        # is not available yet.
        return created, skipped, failed
    elif ip_status == "new":
        print(f"  [NEW]  {hostname:<6} {ip_addr:<18} created")
        created += 1

    # Bring the device's primary_ip4 in line with the SVI IP. This is what
    # makes the device's identity visible at the top of the NetBox device
    # page rather than only on the interface.
    if ip_obj is None:
        return created, skipped, failed
    current_primary = (device.get("primary_ip4") or {}).get("id")
    if current_primary == ip_obj.get("id"):
        skipped += 1
    elif not apply:
        print(f"  [DRY]  {hostname:<6} would set primary_ip4")
        created += 1
    else:
        try:
            client.patch(f"dcim/devices/{device['id']}/",
                         {"primary_ip4": ip_obj["id"]})
            created += 1
        except RuntimeError as exc:
            print(f"  [FAIL] {hostname:<6} primary_ip4, {exc}")
            failed += 1

    return created, skipped, failed


def process_dist(client: NetboxClient, dist_name: str,
                 devices_by_name: dict[str, dict],
                 vlan_id_for_600: int, apply: bool
                 ) -> tuple[int, int, int]:
    """Iterate the dist's table list and assign IPs to each access switch."""
    try:
        info = dist_info_for(client, dist_name)
    except RuntimeError as exc:
        print(f"\n=== {dist_name}, [SKIP] {exc}")
        return 0, 0, 1
    try:
        tables = dist_tables_for(client, dist_name)
    except RuntimeError as exc:
        print(f"\n=== {dist_name}, [SKIP] {exc}")
        return 0, 0, 1
    if not tables:
        print(f"\n=== {dist_name}, [SKIP] no participant tables found")
        return 0, 0, 1

    mgmt_net = ipaddress.ip_network(info["mgmt_v4"])
    hall = dist_name[0]
    print(f"\n=== {dist_name}  (mgmt={info['mgmt_v4']}, hall={hall}) ===")

    totals = [0, 0, 0]
    index = 0
    for table_num, count in tables:
        slots = ["A"] if count == 1 else ["A", "B"]
        for slot in slots:
            hostname = make_access_hostname(hall, table_num, slot)
            octet = OCTET_BASE + index
            ip_addr = f"{mgmt_net.network_address + octet}/{mgmt_net.prefixlen}"
            index += 1

            device = devices_by_name.get(hostname)
            if device is None:
                print(f"  [MISS] {hostname:<6} not found in NetBox, "
                      f"run netbox_create_access_switches.py first")
                totals[2] += 1
                continue

            c, s, f = process_one(client, device, hostname, ip_addr,
                                   vlan_id_for_600, apply)
            totals[0] += c
            totals[1] += s
            totals[2] += f
    return totals[0], totals[1], totals[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually POST or PATCH. Default is dry run.")
    parser.add_argument("--dist", metavar="NAME",
                        help="Only process access switches under this dist.")
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()
    print(f"Mode, {'APPLY' if args.apply else 'DRY-RUN'}")

    vlans = client.get_all("ipam/vlans/")
    vid_to_id = {v["vid"]: v["id"] for v in vlans}
    if MGMT_VLAN_VID not in vid_to_id:
        print(f"Error, VLAN {MGMT_VLAN_VID} not in IPAM.", file=sys.stderr)
        return 1
    vlan_id_for_600 = vid_to_id[MGMT_VLAN_VID]

    access_devices = client.get_all(f"dcim/devices/?role={ROLE_ACCESS}")
    devices_by_name = {d["name"]: d for d in access_devices}
    print(f"Found {len(access_devices)} access devices in NetBox")

    # The dist list comes from NetBox, sorted by name. With `--dist` the
    # set narrows to a single device which is validated before processing.
    if args.dist:
        dists_to_process = [args.dist]
    else:
        dist_devices = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
        dists_to_process = sorted(d["name"] for d in dist_devices)

    totals = [0, 0, 0]
    for dist_name in dists_to_process:
        try:
            c, s, f = process_dist(client, dist_name, devices_by_name,
                                    vlan_id_for_600, args.apply)
            totals[0] += c
            totals[1] += s
            totals[2] += f
        except RuntimeError as exc:
            print(f"\n[FATAL] {dist_name}, {exc}", file=sys.stderr)
            totals[2] += 1

    print()
    print("=" * 60)
    print(f"  {'Created/changed' if args.apply else 'Would create/change'}, {totals[0]}")
    print(f"  Already in place,                  {totals[1]}")
    print(f"  Failed or skipped,                 {totals[2]}")
    print("=" * 60)

    if not args.apply:
        print("\nDry run complete. Re run with --apply to actually assign IPs.")

    return 0 if totals[2] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
