#!/usr/bin/env python3
"""
Inspect NetBox for the dist plan data that `DIST_INFO` and `DIST_TABLES`
currently hold in `netbox_common.py`.

Read only. The script makes no changes, it reports what NetBox already has
so the next step (creating custom fields and filling them) can be sized
accurately.

Sections,
  1. Custom fields that the migration will rely on,
       `district_token` on `dcim.device`
       `switch_count`   on `dcim.rack`
       `vlan_base`      on `dcim.site`
     Reports whether each field exists.
  2. For every dist Device, whether `district_token` is set.
  3. For every Rack used by an access switch, whether `switch_count` is set.
  4. Locations that are home to more than one dist (the `Esport_city`
     ambiguity), since those need a split before the helpers can resolve
     dist to rack unambiguously.
  5. Hall Sites and whether `vlan_base` is set on each.

Usage,
    export NB_TOKEN="..."
    ./netbox_audit_dist_data.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any

from netbox_common import NetboxClient, ROLE_ACCESS, ROLE_DIST, require_token

EXPECTED_FIELDS = {
    "district_token": "dcim.device",
    "switch_count":   "dcim.rack",
    "vlan_base":      "dcim.site",
}


def header(line: str) -> None:
    print()
    print("=" * 72)
    print(f"  {line}")
    print("=" * 72)


def ok(line: str) -> None:
    print(f"  [ ok ]    {line}")


def warn(line: str) -> None:
    print(f"  [warn]    {line}")


def miss(line: str) -> None:
    print(f"  [MISS]    {line}")


def info(line: str) -> None:
    print(f"           {line}")


def audit_custom_fields(client: NetboxClient) -> dict[str, bool]:
    """Return a map of expected field name to whether it exists."""
    header("1.  Custom field availability")
    cfs = client.get_all("extras/custom-fields/")
    present: dict[str, bool] = {name: False for name in EXPECTED_FIELDS}
    for cf in cfs:
        name = cf.get("name")
        if name not in EXPECTED_FIELDS:
            continue
        types = cf.get("object_types") or cf.get("content_types") or []
        expected_type = EXPECTED_FIELDS[name]
        if expected_type in {str(t) for t in types}:
            present[name] = True
            ok(f"field `{name}` exists on {expected_type}")
        else:
            miss(f"field `{name}` exists, but not on {expected_type} (found on {types})")
    for name, exists in present.items():
        if not exists:
            miss(f"field `{name}` not present on {EXPECTED_FIELDS[name]}")
    return present


def audit_dist_district_tokens(client: NetboxClient) -> None:
    header("2.  Dist devices, district_token coverage")
    dists = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
    if not dists:
        miss("no dist devices found, nothing to check")
        return
    set_count = 0
    for d in sorted(dists, key=lambda x: x["name"]):
        token = (d.get("custom_fields") or {}).get("district_token")
        if token:
            ok(f"{d['name']:<22} district_token = {token!r}")
            set_count += 1
        else:
            miss(f"{d['name']:<22} district_token is not set")
    info(f"{set_count}/{len(dists)} dists have district_token set")


def audit_rack_switch_counts(client: NetboxClient) -> None:
    header("3.  Racks used by access switches, switch_count coverage")
    access_devices = client.get_all(f"dcim/devices/?role={ROLE_ACCESS}")
    if not access_devices:
        miss("no access switch devices found, nothing to check")
        return
    racks_in_use: dict[int, dict[str, Any]] = {}
    access_per_rack: dict[int, int] = defaultdict(int)
    for d in access_devices:
        rack = d.get("rack") or {}
        rid = rack.get("id")
        if rid is None:
            continue
        racks_in_use[rid] = rack
        access_per_rack[rid] += 1
    if not racks_in_use:
        miss("access switches found but none are placed in a rack, "
             "switch_count check skipped")
        return
    # Fetch the full rack objects to inspect custom fields. The reference
    # nested inside the device payload does not include custom_fields.
    full_racks = {r["id"]: r for r in client.get_all("dcim/racks/")}
    set_count = 0
    for rid in sorted(racks_in_use):
        rack = full_racks.get(rid, racks_in_use[rid])
        name = rack.get("name", f"id={rid}")
        sc = (rack.get("custom_fields") or {}).get("switch_count")
        actual = access_per_rack[rid]
        if sc is None:
            miss(f"rack {name:<10} switch_count not set, observed access switches = {actual}")
        else:
            if int(sc) != actual:
                warn(f"rack {name:<10} switch_count = {sc} but observed access switches = {actual}")
            else:
                ok(f"rack {name:<10} switch_count = {sc}")
            set_count += 1
    info(f"{set_count}/{len(racks_in_use)} racks have switch_count set")


def audit_dist_location_ambiguity(client: NetboxClient) -> None:
    header("4.  Locations that host more than one dist")
    dists = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
    by_location: dict[str, list[str]] = defaultdict(list)
    for d in dists:
        loc = (d.get("location") or {}).get("name")
        if loc:
            by_location[loc].append(d["name"])
    ambiguous = {loc: ns for loc, ns in by_location.items() if len(ns) > 1}
    if not ambiguous:
        ok("every location hosts at most one dist, no ambiguity to resolve")
        return
    for loc, names in ambiguous.items():
        miss(f"location {loc!r} hosts {len(names)} dists, {names}")
        info("split this Location into one Location per dist, or add a "
             "served_by_dist custom field on each Rack, so the helper can "
             "tell which dist serves which rack")


def audit_site_vlan_bases(client: NetboxClient) -> None:
    header("5.  Hall Sites, vlan_base coverage")
    sites = client.get_all("dcim/sites/")
    if not sites:
        miss("no sites found")
        return
    for s in sorted(sites, key=lambda x: x["slug"]):
        vb = (s.get("custom_fields") or {}).get("vlan_base")
        if vb is None:
            info(f"  site {s['slug']:<12} vlan_base not set (only matters once "
                 f"this site hosts a dist that serves tables)")
        else:
            ok(f"site {s['slug']:<12} vlan_base = {vb}")


def main() -> int:
    if not require_token():
        return 1
    client = NetboxClient()
    try:
        audit_custom_fields(client)
        audit_dist_district_tokens(client)
        audit_rack_switch_counts(client)
        audit_dist_location_ambiguity(client)
        audit_site_vlan_bases(client)
    except RuntimeError as exc:
        print(f"\nFATAL, {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
