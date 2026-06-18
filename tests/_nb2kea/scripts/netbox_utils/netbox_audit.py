#!/usr/bin/env python3
"""
NetBox data audit for the Glitched 2026 config renderer.

Pulls inventory and topology data the renderer will need, summarises what is
present, and flags what is missing. Output is intentionally readable so a
human can scan it, the optional `--json` flag dumps the raw API responses
for downstream ingestion by the renderer or by an LLM session.

Usage,
    export NB_TOKEN="..."
    ./netbox_audit.py
    ./netbox_audit.py --json /tmp/nb-audit.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from typing import Any

from netbox_common import (
    NetboxClient,
    ROLE_ACCESS,
    ROLE_DIST,
    require_token,
)

# Sites the audit expects, one per hall. Site `c` carries Hall C, the old
# typo of `v` is no longer accepted as a substitute.
EXPECTED_HALL_SITE_SLUGS = {"a", "b", "c", "d", "jf", "ln", "ls", "u4", "mg"}

# Device role slugs the renderer cares about. The slugs match what is
# already configured in NetBox after the 2026-06-08 cleanup. The audit only
# warns on missing roles, it does not enforce a particular spelling.
EXPECTED_DEVICE_ROLE_SLUGS = {ROLE_DIST, ROLE_ACCESS, "core_routers"}

# Required physical interfaces on each dist after the device-type swap to
# EX4300-24T. The audit reports any that are missing per dist.
EXPECTED_DIST_INTERFACE_NAMES = {
    "lo0.0",
    "irb.600",
    "irb.1100",
    "irb.1101",
    "irb.1200",
    "irb.1201",
    "xe-0/2/0",
    "xe-0/2/1",
}

# Prefixes the renderer reads at build time. Their absence indicates a gap
# in IPAM that should be filled before rendering.
EXPECTED_PREFIXES = [
    "172.16.255.0/24",
    "100.65.0.0/26",
    "100.65.1.0/26",
    "100.66.0.0/26",
    "100.66.1.0/26",
    "92.33.58.96/27",
]

EXPECTED_VLAN_VIDS = {1, 500, 501, 600, 1100, 1101, 1200, 1201}


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def header(line: str) -> None:
    print()
    print("=" * 72)
    print(f"  {line}")
    print("=" * 72)


def subheader(line: str) -> None:
    print()
    print(f"--- {line} ---")


def ok(line: str) -> None:
    print(f"  [ ok ]    {line}")


def warn(line: str) -> None:
    print(f"  [warn]    {line}")


def miss(line: str) -> None:
    print(f"  [MISS]    {line}")


def info(line: str) -> None:
    print(f"           {line}")


# ---------------------------------------------------------------------------
# Audit sections
# ---------------------------------------------------------------------------

def audit_api(raw: dict, client: NetboxClient) -> None:
    header("1.  API reachability + version")
    data = client.get_one("status")
    raw["status"] = data
    if isinstance(data, dict):
        if "netbox-version" in data:
            ok(f"NetBox version, {data['netbox-version']}")
        if "python-version" in data:
            info(f"Python, {data['python-version']}")
        if "plugins" in data:
            info(f"Plugins, {list(data['plugins'].keys()) or 'none'}")
    else:
        warn(f"/api/status returned non dict, {type(data).__name__}")


def audit_custom_fields(raw: dict, client: NetboxClient) -> None:
    header("2.  Custom field definitions")
    custom_fields = client.get_all("extras/custom-fields/")
    raw["custom_fields"] = custom_fields
    if not custom_fields:
        warn("No custom fields defined.")
        info("Renderer does not strictly need any, `boot_phase` on access")
        info("switches and `dhcp_circuit_id` on dist interfaces are recommended.")
        return
    ok(f"{len(custom_fields)} custom fields defined.")
    for field in custom_fields:
        models = ", ".join(field.get("content_types") or field.get("object_types") or [])
        info(f"  {field.get('name')!s:<30} type={field.get('type', {}).get('value', '?')!s:<10} on={models}")


def audit_tags(raw: dict, client: NetboxClient) -> None:
    header("3.  Tags (informational)")
    tags = client.get_all("extras/tags/")
    raw["tags"] = tags
    slugs = {t["slug"] for t in tags}
    ok(f"{len(tags)} tags total.")
    info(f"tags, {', '.join(sorted(slugs)) or '(none)'}")
    info("(Renderer filters devices by role, not tag.)")


def audit_roles(raw: dict, client: NetboxClient) -> None:
    header("4.  Device roles")
    roles = client.get_all("dcim/device-roles/")
    raw["device_roles"] = roles
    slugs = {r["slug"] for r in roles}
    ok(f"{len(roles)} device roles total.")
    for slug in sorted(EXPECTED_DEVICE_ROLE_SLUGS):
        if slug in slugs:
            ok(f"role present, {slug}")
        else:
            miss(f"role missing, {slug}")
    extra = sorted(slugs - EXPECTED_DEVICE_ROLE_SLUGS)
    if extra:
        info(f"other roles, {', '.join(extra)}")


def audit_sites_locations_racks(raw: dict, client: NetboxClient) -> None:
    header("5.  Sites, locations, racks (physical hierarchy)")

    sites = client.get_all("dcim/sites/")
    raw["sites"] = sites
    ok(f"{len(sites)} sites.")
    site_slugs = {s["slug"] for s in sites}
    for s in sites:
        info(f"  site, {s['slug']:<20} ({s.get('name')})")

    missing_halls = sorted(EXPECTED_HALL_SITE_SLUGS - site_slugs)
    if missing_halls:
        warn(f"expected hall sites missing, {missing_halls}")
    if "c" not in site_slugs and "v" in site_slugs:
        warn("site slug 'v' present but 'c' is missing, likely a typo for Hall-C")
    if "elmia" in site_slugs:
        warn("site 'elmia' present alongside per-hall sites, pick one model")

    locations = client.get_all("dcim/locations/")
    raw["locations"] = locations
    ok(f"{len(locations)} locations.")
    by_site = defaultdict(list)
    for loc in locations:
        site_slug = (loc.get("site") or {}).get("slug", "?")
        by_site[site_slug].append(loc["name"])
    for site_slug, names in sorted(by_site.items()):
        info(f"  {site_slug}, {', '.join(names)}")
    # A location name that appears under more than one site is almost
    # always a leftover from a draft that should be cleaned up.
    name_to_sites = defaultdict(set)
    for loc in locations:
        name_to_sites[loc["name"]].add((loc.get("site") or {}).get("slug", "?"))
    dupes = {n: s for n, s in name_to_sites.items() if len(s) > 1}
    if dupes:
        warn(f"locations duplicated across sites, {dupes}")

    racks = client.get_all("dcim/racks/")
    raw["racks"] = racks
    ok(f"{len(racks)} racks total.")
    dist_racks = sorted(r["name"] for r in racks if r["name"].endswith(("_Dist", "_DIST", "-Dist", "-DIST")))
    other_racks = sorted(r["name"] for r in racks if r["name"] not in dist_racks)
    info(f"  dist style racks (heuristic, name ends *_Dist), {len(dist_racks)}")
    if dist_racks:
        info(f"    {', '.join(dist_racks[:12])}{'...' if len(dist_racks) > 12 else ''}")
    info(f"  other racks (probably tables), {len(other_racks)}")
    if other_racks:
        info(f"    sample, {', '.join(other_racks[:20])}{'...' if len(other_racks) > 20 else ''}")
    roles_seen = Counter((r.get("role") or {}).get("slug") or "<none>" for r in racks)
    info(f"  rack role usage, {dict(roles_seen)}")


def audit_devices(raw: dict, client: NetboxClient) -> dict[str, list[dict]]:
    header("6.  Devices (grouped by role)")
    devices = client.get_all("dcim/devices/")
    raw["devices"] = devices
    by_role: dict[str, list[dict]] = defaultdict(list)
    for d in devices:
        role = (d.get("role") or d.get("device_role") or {}).get("slug", "<no-role>")
        by_role[role].append(d)
    for role in sorted(by_role):
        marker = "*" if role in EXPECTED_DEVICE_ROLE_SLUGS else " "
        ok(f"{marker} role={role}, {len(by_role[role])} devices")
        if role in EXPECTED_DEVICE_ROLE_SLUGS:
            for d in sorted(by_role[role], key=lambda x: x["name"]):
                ip4 = ((d.get("primary_ip4") or {}).get("address")) or "no-IP"
                site = (d.get("site") or {}).get("slug", "?")
                loc = (d.get("location") or {}).get("name", "?")
                info(f"      {d['name']:<28} site={site:<6} loc={loc:<18} primary_ip4={ip4}")
            no_primary_ip = [d for d in by_role[role] if not d.get("primary_ip4")]
            if no_primary_ip:
                warn(f"  {len(no_primary_ip)}/{len(by_role[role])} devices in role {role} have no primary_ip4")

    return by_role


def audit_dist_switches(raw: dict, dist_devices: list[dict], client: NetboxClient) -> None:
    header("7.  Distribution switches, interfaces, IPs, cables")
    if not dist_devices:
        miss("No devices with role distribution_switches found, cannot audit dists.")
        return

    dist_audit = []
    for d in dist_devices:
        name = d["name"]
        dev_id = d["id"]
        subheader(f"dist, {name}")

        ifaces = client.get_all(f"dcim/interfaces/?device_id={dev_id}")
        ips = client.get_all(f"ipam/ip-addresses/?device_id={dev_id}")

        ip_by_iface: dict[int, list[str]] = defaultdict(list)
        for ip in ips:
            ao = ip.get("assigned_object")
            if ao and ao.get("id") is not None:
                ip_by_iface[ao["id"]].append(ip["address"])

        iface_names = {i["name"] for i in ifaces}

        missing_ifaces = EXPECTED_DIST_INTERFACE_NAMES - iface_names
        if missing_ifaces:
            for n in sorted(missing_ifaces):
                miss(f"interface missing, {n}")
        else:
            ok("all expected baseline interfaces present")

        ge_ifaces = [i for i in ifaces if i["name"].startswith("ge-0/0/")]
        ge_with_desc = [i for i in ge_ifaces if i.get("description")]
        if not ge_ifaces:
            miss("no ge-0/0/x interfaces at all")
        else:
            info(f"ge-0/0/x interfaces, {len(ge_ifaces)} total, {len(ge_with_desc)} with description")
            sample = [f"{i['name']} '{i.get('description','')}'" for i in ge_with_desc[:3]]
            if sample:
                info(f"  sample, {sample}")

        for needed in sorted(EXPECTED_DIST_INTERFACE_NAMES):
            iface = next((i for i in ifaces if i["name"] == needed), None)
            if iface is None:
                continue
            ip_list = ip_by_iface.get(iface["id"], [])
            if not ip_list and needed.startswith(("irb.", "lo0.")):
                miss(f"interface {needed} has no IP assigned")
            elif ip_list:
                info(f"  {needed:<10}, {', '.join(ip_list)}")

        ge_cabled = sum(1 for i in ge_ifaces if i.get("cable"))
        info(f"  cabled ge-0/0/x, {ge_cabled}")

        dist_audit.append({
            "device": d,
            "interfaces": ifaces,
            "ip_addresses": ips,
        })

    raw["dist_audit"] = dist_audit


def audit_access_switches(raw: dict, access_devices: list[dict], client: NetboxClient) -> None:
    header("8.  Access switches, primary IP and uplink cable spot check")
    if not access_devices:
        miss("No devices with role access_switch found, cannot audit access.")
        return

    ok(f"{len(access_devices)} access-switch devices.")

    no_primary = [d for d in access_devices if not d.get("primary_ip4")]
    no_rack = [d for d in access_devices if not d.get("rack")]
    no_pos = [d for d in access_devices if d.get("position") is None]
    if no_primary:
        warn(f"{len(no_primary)} access switches have no primary_ip4")
    if no_rack:
        warn(f"{len(no_rack)} access switches have no rack assigned")
    if no_pos:
        info(f"{len(no_pos)} access switches have no position (expected for single-switch tables)")

    sample = access_devices[:5]
    access_audit = []
    for d in sample:
        ifaces = client.get_all(f"dcim/interfaces/?device_id={d['id']}")
        gi02 = next(
            (i for i in ifaces if i["name"] in ("GigabitEthernet0/2", "Gi0/2", "gi0/2")),
            None,
        )
        info(f"  {d['name']}, ")
        if gi02 is None:
            miss(f"    no Gi0/2 interface on {d['name']}")
        elif not gi02.get("cable"):
            miss(f"    {d['name']} Gi0/2 not cabled")
        else:
            cable_field = gi02.get("cable")
            cable_id = cable_field.get("id") if isinstance(cable_field, dict) else cable_field
            info(f"    Gi0/2 cabled (cable id={cable_id})")
        access_audit.append({"device": d, "gi02": gi02})

    raw["access_audit"] = access_audit


def audit_cables(raw: dict, client: NetboxClient) -> None:
    header("9.  Cables")
    cables = client.get_all("dcim/cables/")
    raw["cables"] = cables
    ok(f"{len(cables)} cables total.")
    type_counts = Counter(c.get("type") or "<none>" for c in cables)
    for t, n in type_counts.most_common():
        info(f"  type={t}, {n}")


def audit_vlans(raw: dict, client: NetboxClient) -> None:
    header("10. VLANs (IPAM)")
    vlans = client.get_all("ipam/vlans/")
    raw["vlans"] = vlans
    ok(f"{len(vlans)} VLANs total.")
    vids = {v["vid"] for v in vlans}

    for vid in sorted(EXPECTED_VLAN_VIDS):
        if vid in vids:
            ok(f"VLAN {vid} present")
        else:
            miss(f"VLAN {vid} missing")

    d_hall_range = set(range(201, 257))
    present_d = d_hall_range & vids
    info(f"  D-hall participant VLANs (201..256) present, {len(present_d)}/56")
    if present_d:
        missing_d = sorted(d_hall_range - vids)
        if missing_d:
            info(f"    missing, {missing_d[:10]}{'...' if len(missing_d) > 10 else ''}")


def audit_prefixes(raw: dict, client: NetboxClient) -> None:
    header("11. Prefixes (IPAM)")
    prefixes = client.get_all("ipam/prefixes/")
    raw["prefixes"] = prefixes
    ok(f"{len(prefixes)} prefixes total.")
    present = {p["prefix"] for p in prefixes}
    for expected in EXPECTED_PREFIXES:
        if expected in present:
            ok(f"prefix present, {expected}")
        else:
            miss(f"prefix missing, {expected}")

    v4 = [p for p in prefixes if p.get("family", {}).get("value") == 4]
    v6 = [p for p in prefixes if p.get("family", {}).get("value") == 6]
    info(f"  v4, {len(v4)}, v6, {len(v6)}")

    p26 = [p["prefix"] for p in v4 if p["prefix"].endswith("/26")]
    if p26:
        info(f"  v4 /26 prefixes ({len(p26)}), {p26[:6]}{'...' if len(p26) > 6 else ''}")


def audit_summary(raw: dict) -> None:
    header("12. Summary")
    dist_count = sum(
        1
        for d in raw.get("devices", [])
        if (d.get("role") or d.get("device_role") or {}).get("slug") == ROLE_DIST
    )
    access_count = sum(
        1
        for d in raw.get("devices", [])
        if (d.get("role") or d.get("device_role") or {}).get("slug") == ROLE_ACCESS
    )
    info(f"{ROLE_DIST + ' devices:':<30} {dist_count}  (expect at least 19)")
    info(f"{ROLE_ACCESS + ' devices:':<30} {access_count}  (expect at least 110)")
    info(f"{'cables:':<30} {len(raw.get('cables', []))}")
    info(f"{'vlans:':<30} {len(raw.get('vlans', []))}")
    info(f"{'prefixes:':<30} {len(raw.get('prefixes', []))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="Dump all collected raw API data to this JSON file.",
    )
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()
    raw: dict[str, Any] = {}

    try:
        audit_api(raw, client)
        audit_custom_fields(raw, client)
        audit_tags(raw, client)
        audit_roles(raw, client)
        audit_sites_locations_racks(raw, client)
        by_role = audit_devices(raw, client)
        audit_dist_switches(raw, by_role.get(ROLE_DIST, []), client)
        audit_access_switches(raw, by_role.get(ROLE_ACCESS, []), client)
        audit_cables(raw, client)
        audit_vlans(raw, client)
        audit_prefixes(raw, client)
        audit_summary(raw)
    except RuntimeError as exc:
        print(f"\nFATAL, {exc}", file=sys.stderr)
        return 2

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(raw, fh, indent=2, default=str)
        print()
        print(f"Raw API data dumped to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
