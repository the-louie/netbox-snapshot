#!/usr/bin/env python3
"""
Bulk create the access switches in NetBox, one or two per participant
table, named per the convention agreed 2026-06-08,

    <hall-letter><2-digit-table-number><slot-letter>

    Single switch tables always get slot 'A'  (for example D48A)
    Two switch tables get 'A' and 'B'         (for example D39A, D39B)
    Going from 1 to 2 switches later means adding 'B', no rename.

For each dist, the (table, switch_count) list is read from NetBox through
`dist_tables_for`, which walks the racks at the dist's Location and reads
the `switch_count` custom field on each. The rack order is the natural
numeric sort of the rack name, which matches the ge-0/0/x port order on
the dist for every existing district.

The new device's site, location, and rack are inherited from the home
dist and the matching NetBox rack. Idempotent, names that already exist
are left alone. IP allocation is a separate pass.

Usage,
    export NB_TOKEN="..."
    ./netbox_create_access_switches.py                          # dry run
    ./netbox_create_access_switches.py --apply
    ./netbox_create_access_switches.py --dist D-THE-FORGE-SW --apply
"""

from __future__ import annotations

import argparse
import sys

from netbox_common import (
    NetboxClient,
    ROLE_ACCESS,
    ROLE_DIST,
    dist_tables_for,
    make_access_hostname,
    require_token,
)

DEVICE_TYPE_SLUG = "ws-c2950t-24"
DEFAULT_STATUS = "active"


def lookup_id(client: NetboxClient, endpoint: str, query: str) -> int | None:
    """Resolve a NetBox object's numeric id by a single filter."""
    data = client.get_one(f"{endpoint}/?{query}")
    matches = (data or {}).get("results", [])
    if len(matches) == 1:
        return matches[0]["id"]
    return None


def fetch_rack_id(client: NetboxClient, rack_name: str, site_id: int,
                  rack_cache: dict) -> int | None:
    """
    Look up rack by name within a site. Caches per site rack lookups so a
    multi dist run does not refetch the same rack list more than once.
    """
    if site_id not in rack_cache:
        racks = client.get_all(f"dcim/racks/?site_id={site_id}")
        rack_cache[site_id] = {r["name"]: r["id"] for r in racks}
    return rack_cache[site_id].get(rack_name)


def process_dist(client: NetboxClient,
                 dist: dict,
                 device_type_id: int,
                 access_role_id: int,
                 existing_access_names: set[str],
                 rack_cache: dict,
                 apply: bool) -> tuple[int, int, int]:
    """Return (created, skipped_existing, failed)."""
    name = dist["name"]
    try:
        tables = dist_tables_for(client, name)
    except RuntimeError as exc:
        print(f"\n=== {name}, [SKIP] {exc}")
        return 0, 0, 1
    if not tables:
        print(f"\n=== {name}, [SKIP] no participant tables resolved from NetBox")
        return 0, 0, 1

    site = dist.get("site") or {}
    location = dist.get("location") or {}
    site_id = site.get("id")
    location_id = location.get("id")
    if not site_id or not location_id:
        print(f"\n=== {name}, [SKIP] dist has no site or location set")
        return 0, 0, 1

    # The hall letter is the first character of the dist name, the rack
    # names are unpadded (D1, D2, C3, ...) which matches NetBox state.
    hall = name[0]
    print(f"\n=== {name}  (hall={hall}, site={site.get('slug')}, "
          f"location={location.get('name')}) ===")

    created = skipped = failed = 0

    for table_num, switch_count in tables:
        slots = ["A"] if switch_count == 1 else ["A", "B"]
        rack_name = f"{hall}{table_num}"
        rack_id = fetch_rack_id(client, rack_name, site_id, rack_cache)
        if rack_id is None:
            print(f"  [MISS] rack {rack_name!r} not found in site "
                  f"{site.get('slug')!r}, skipping table")
            failed += len(slots)
            continue

        for slot in slots:
            hostname = make_access_hostname(hall, table_num, slot)
            if hostname in existing_access_names:
                print(f"  [ok]   {hostname:<6} already exists")
                skipped += 1
                continue

            body = {
                "name": hostname,
                "role": access_role_id,
                "device_type": device_type_id,
                "site": site_id,
                "location": location_id,
                "rack": rack_id,
                "status": DEFAULT_STATUS,
            }

            if not apply:
                print(f"  [DRY]  {hostname:<6} would create "
                      f"(rack={rack_name}, slot={slot})")
                created += 1
                continue

            try:
                created_obj = client.post("dcim/devices/", body)
                print(f"  [NEW]  {hostname:<6} created  "
                      f"(rack={rack_name}, slot={slot}, id={created_obj['id']})")
                existing_access_names.add(hostname)
                created += 1
            except RuntimeError as exc:
                print(f"  [FAIL] {hostname:<6} {exc}")
                failed += 1

    return created, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually POST device creates. Default is dry run.")
    parser.add_argument("--dist", metavar="NAME",
                        help="Only process access switches under this dist.")
    parser.add_argument("--device-type", default=DEVICE_TYPE_SLUG,
                        help=f"Device-type slug for access switches "
                             f"(default, {DEVICE_TYPE_SLUG}).")
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()
    print(f"Mode, {'APPLY' if args.apply else 'DRY-RUN'}")

    # Resolve dependencies up front so the run fails fast on a missing
    # device type or role.
    device_type_id = lookup_id(client, "dcim/device-types",
                                f"slug={args.device_type}")
    if device_type_id is None:
        print(f"Error, device-type slug {args.device_type!r} not found in "
              f"NetBox. Import it from the community library first.",
              file=sys.stderr)
        return 1
    print(f"Device-type {args.device_type!r}, id {device_type_id}")

    access_role_id = lookup_id(client, "dcim/device-roles",
                                f"slug={ROLE_ACCESS}")
    if access_role_id is None:
        print(f"Error, device-role slug {ROLE_ACCESS!r} not found.",
              file=sys.stderr)
        return 1
    print(f"Role {ROLE_ACCESS!r}, id {access_role_id}")

    dists = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
    if args.dist:
        dists = [d for d in dists if d["name"] == args.dist]
        if not dists:
            print(f"Error, no dist {args.dist!r} found.", file=sys.stderr)
            return 1
    print(f"Found {len(dists)} dist device(s)")

    # The idempotency check needs every existing access hostname up front
    # so the per dist loop does not query NetBox for each name.
    existing = client.get_all(f"dcim/devices/?role={ROLE_ACCESS}")
    existing_names = {d["name"] for d in existing}
    print(f"Existing access switches in NetBox, {len(existing_names)}")

    rack_cache: dict = {}

    totals = [0, 0, 0]
    for dist in sorted(dists, key=lambda d: d["name"]):
        try:
            c, s, f = process_dist(client, dist, device_type_id,
                                    access_role_id, existing_names,
                                    rack_cache, args.apply)
            totals[0] += c
            totals[1] += s
            totals[2] += f
        except RuntimeError as exc:
            print(f"\n[FATAL] {dist['name']}, {exc}", file=sys.stderr)
            totals[2] += 1

    print()
    print("=" * 60)
    print(f"  {'Created' if args.apply else 'Would create'}, {totals[0]}")
    print(f"  Already existed,               {totals[1]}")
    print(f"  Failed or skipped,             {totals[2]}")
    print("=" * 60)

    if not args.apply:
        print("\nDry run complete. Re run with --apply to actually create devices.")

    return 0 if totals[2] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
