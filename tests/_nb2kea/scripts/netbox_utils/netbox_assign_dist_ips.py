#!/usr/bin/env python3
"""
Assign IPs to the virtual interfaces previously created on each dist switch.

For each device with role 'distribution_switches',
  lo0.0       <- 172.16.255.<octet>/32       (loopback, octet from dist_info_for)
  irb.600     <- <per-dist /24>.1/24         (mgmt VLAN 600 SVI, .1 host)
  irb.1100    <- 100.65.0.<octet>/26         (OSPF default to MX-01)
  irb.1101    <- 100.65.1.<octet>/26         (OSPF internet to MX-01)
  irb.1200    <- 100.66.0.<octet>/26         (OSPF default to MX-02)
  irb.1201    <- 100.66.1.<octet>/26         (OSPF internet to MX-02)
  IPv6 siblings for the four OSPF linknets
  irb.<vlan>  <- .1 of the /26 IPAM linked to <vlan> (per participant VLAN)

After all IPs are created, sets `Device.primary_ip4` to the loopback
IPAddress.

The per dist mgmt /24 and the loopback octet come from `dist_info_for` in
the shared module, the script no longer carries a local mapping.

Idempotent. Existing IPs on the same interface with the same address are
left alone. Re runs only fill in what is missing.

Usage,
    export NB_TOKEN="..."
    ./netbox_assign_dist_ips.py                          # dry run (default)
    ./netbox_assign_dist_ips.py --apply                  # actually POST
    ./netbox_assign_dist_ips.py --dist D-THE-FORGE-SW --apply
"""

from __future__ import annotations

import argparse
import ipaddress
import sys

from netbox_common import (
    NetboxClient,
    ROLE_DIST,
    dist_info_for,
    require_token,
)

# Fleet wide pools. These prefixes are agreed in the architecture notes
# and would only change with a renumbering project.
LOOPBACK_V4_PARENT = ipaddress.ip_network("172.16.255.0/24")
OSPF_V4_LINKNETS = {
    1100: ipaddress.ip_network("100.65.0.0/26"),
    1101: ipaddress.ip_network("100.65.1.0/26"),
    1200: ipaddress.ip_network("100.66.0.0/26"),
    1201: ipaddress.ip_network("100.66.1.0/26"),
}
OSPF_V6_LINKNETS = {
    1100: ipaddress.ip_network("2a02:1420:1c0:1100::/64"),
    1101: ipaddress.ip_network("2a02:1420:1c0:1101::/64"),
    1200: ipaddress.ip_network("2a02:1420:1c0:1200::/64"),
    1201: ipaddress.ip_network("2a02:1420:1c0:1201::/64"),
}


def host_in_net(net: ipaddress._BaseNetwork, host_bit: int) -> str:
    """e.g. host_in_net(100.65.0.0/26, 4) -> '100.65.0.4/26'."""
    addr = net.network_address + host_bit
    if addr not in net:
        raise ValueError(f"host bit {host_bit} not in {net}")
    return f"{addr}/{net.prefixlen}"


def first_usable_dot1(prefix_str: str) -> str:
    """Return '<network>+1/<plen>' for any prefix size."""
    net = ipaddress.ip_network(prefix_str, strict=False)
    return f"{net.network_address + 1}/{net.prefixlen}"


def ensure_ip(client: NetboxClient, address: str, iface_id: int,
              description: str, existing_by_iface: dict[int, list[dict]],
              apply: bool) -> tuple[str, dict | None]:
    """
    Make sure `address` is bound to `iface_id`. Returns (status, ip_object)
    where status is one of 'new', 'exists', 'adopt', 'conflict', 'dry',
    'dry_adopt'.
    """
    # Already on this interface keeps re runs cheap.
    for ip in existing_by_iface.get(iface_id, []):
        if ip["address"] == address:
            return "exists", ip

    # The address may already exist in IPAM under three different shapes.
    found = client.get_one(f"ipam/ip-addresses/?address={address}")
    matches = (found or {}).get("results", []) if isinstance(found, dict) else []
    if matches:
        # 1, already bound to this interface (race or earlier partial run).
        for ip in matches:
            ao = ip.get("assigned_object")
            if ao and ao.get("id") == iface_id:
                return "exists", ip
        # 2, exists but unassigned, adopt it onto this interface.
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
        # 3, bound to a different interface, real conflict.
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


def process_dist(client: NetboxClient, device: dict,
                 apply: bool) -> tuple[int, int, int]:
    """Return (created, skipped_existing, failed)."""
    name = device["name"]
    try:
        info = dist_info_for(client, name)
    except RuntimeError as exc:
        print(f"\n=== {name}, [SKIP] {exc}")
        return 0, 0, 1

    octet = info["loopback_octet"]
    print(f"\n=== {name}  (octet={octet}, mgmt={info['mgmt_v4']}) ===")

    ifaces = client.get_all(f"dcim/interfaces/?device_id={device['id']}")
    by_name = {i["name"]: i for i in ifaces}
    existing_ips = client.get_all(f"ipam/ip-addresses/?device_id={device['id']}")
    existing_by_iface: dict[int, list[dict]] = {}
    for ip in existing_ips:
        ao = ip.get("assigned_object")
        if ao and ao.get("id"):
            existing_by_iface.setdefault(ao["id"], []).append(ip)

    created = skipped = failed = 0
    loopback_ip_obj: dict | None = None

    # Build the assignment plan before executing. Each entry is
    # (interface_name, address with prefix length, description).
    plan: list[tuple[str, str, str]] = []

    plan.append(("lo0.0",
                 f"{LOOPBACK_V4_PARENT.network_address + octet}/32",
                 f"{name} loopback"))

    mgmt_net = ipaddress.ip_network(info["mgmt_v4"])
    plan.append(("irb.600",
                 f"{mgmt_net.network_address + 1}/{mgmt_net.prefixlen}",
                 f"{name} mgmt SVI"))

    for vid, net in OSPF_V4_LINKNETS.items():
        plan.append((f"irb.{vid}", host_in_net(net, octet),
                     f"OSPF linknet VLAN {vid}"))
    for vid, net in OSPF_V6_LINKNETS.items():
        plan.append((f"irb.{vid}", host_in_net(net, octet),
                     f"OSPF linknet VLAN {vid} (v6)"))

    # Participant IRBs are discovered from the device's existing virtual
    # interfaces. Each has the VLAN id baked into the name (irb.<vid>),
    # the matching /26 is looked up in IPAM by VLAN binding.
    participant_irbs = [
        n for n in by_name
        if n.startswith("irb.") and
           n not in {f"irb.{v}" for v in (600, 1100, 1101, 1200, 1201)}
    ]
    for iface_name in sorted(participant_irbs,
                              key=lambda x: int(x.split(".")[1])):
        vlan_vid = int(iface_name.split(".")[1])
        prefixes = client.get_all(f"ipam/prefixes/?vlan_vid={vlan_vid}")
        v4_prefix = next(
            (p for p in prefixes if p.get("family", {}).get("value") == 4),
            None,
        )
        if not v4_prefix:
            print(f"  [SKIP] {iface_name:<12} no IPAM /26 found for VLAN {vlan_vid}")
            failed += 1
            continue
        addr = first_usable_dot1(v4_prefix["prefix"])
        plan.append((iface_name, addr,
                     f"{name} participant IRB VLAN {vlan_vid}"))

    for iface_name, addr, desc in plan:
        iface = by_name.get(iface_name)
        if not iface:
            print(f"  [SKIP] {iface_name:<12} interface not found "
                  f"(run netbox_create_dist_virtual_ifaces.py first)")
            failed += 1
            continue
        try:
            status, ip_obj = ensure_ip(client, addr, iface["id"], desc,
                                        existing_by_iface, apply)
        except RuntimeError as exc:
            print(f"  [FAIL] {iface_name:<12} {addr:<25} {exc}")
            failed += 1
            continue

        if status == "exists":
            print(f"  [ok]   {iface_name:<12} {addr:<25} already assigned")
            skipped += 1
            if iface_name == "lo0.0":
                loopback_ip_obj = ip_obj
        elif status == "adopt":
            print(f"  [ADP]  {iface_name:<12} {addr:<25} adopted (was unassigned)")
            created += 1
            if iface_name == "lo0.0":
                loopback_ip_obj = ip_obj
        elif status == "dry_adopt":
            print(f"  [DRY]  {iface_name:<12} would adopt existing {addr} "
                  f"(currently unassigned)")
            created += 1
        elif status == "conflict":
            other = (ip_obj or {}).get("assigned_object") or {}
            other_iface = other.get("name", "?")
            other_dev = (other.get("device") or {}).get("name", "?")
            print(f"  [CFLT] {iface_name:<12} {addr:<25} "
                  f"already on {other_dev}/{other_iface}")
            failed += 1
        elif status == "dry":
            print(f"  [DRY]  {iface_name:<12} would assign {addr}  desc='{desc}'")
            created += 1
        elif status == "new":
            print(f"  [NEW]  {iface_name:<12} {addr:<25} created")
            created += 1
            if iface_name == "lo0.0":
                loopback_ip_obj = ip_obj
        else:
            print(f"  [???]  {iface_name:<12} {addr:<25} status={status}")

    # The device's primary_ip4 should track the loopback so the rest of
    # NetBox surfaces the canonical management address.
    if loopback_ip_obj is None and apply:
        print(f"  [warn] no loopback IP object to set as primary_ip4 "
              f"(lo0.0 may have failed)")
    elif loopback_ip_obj is not None:
        current_primary = (device.get("primary_ip4") or {}).get("id")
        if current_primary == loopback_ip_obj["id"]:
            print(f"  [ok]   primary_ip4 already set to lo0.0")
        elif not apply:
            print(f"  [DRY]  would set primary_ip4 to "
                  f"{loopback_ip_obj.get('address', '<dry>')}")
            created += 1
        else:
            try:
                client.patch(f"dcim/devices/{device['id']}/",
                             {"primary_ip4": loopback_ip_obj["id"]})
                print(f"  [NEW]  primary_ip4 set to {loopback_ip_obj['address']}")
                created += 1
            except RuntimeError as exc:
                print(f"  [FAIL] setting primary_ip4, {exc}")
                failed += 1

    return created, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually POST or PATCH. Default is dry run.")
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

    totals = [0, 0, 0]
    for device in sorted(devices, key=lambda d: d["name"]):
        try:
            c, s, f = process_dist(client, device, args.apply)
            totals[0] += c
            totals[1] += s
            totals[2] += f
        except RuntimeError as exc:
            print(f"\n[FATAL] {device['name']}, {exc}", file=sys.stderr)
            totals[2] += 1

    print()
    print("=" * 60)
    print(f"  {'Created/changed' if args.apply else 'Would create/change'}, "
          f"{totals[0]}")
    print(f"  Already in place,                  {totals[1]}")
    print(f"  Failed or skipped,                 {totals[2]}")
    print("=" * 60)

    if not args.apply:
        print("\nDry run complete. Re run with --apply to actually assign IPs.")

    return 0 if totals[2] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
