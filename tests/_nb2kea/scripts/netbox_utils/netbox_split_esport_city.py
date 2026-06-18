#!/usr/bin/env python3
"""
Split the `Esport_city` Location into `Esport_city_1` and `Esport_city_2`
so each location hosts exactly one dist.

Source of the split,
  * `C-ESPORTS-CITY-1-SW` keeps racks C4 to C7 in `Esport_city_1`.
  * `C-ESPORTS-CITY-2-SW` and racks C8 to C15 move to `Esport_city_2`.

The script,
  1. Renames the existing `Esport_city` Location to `Esport_city_1` if it
     has not been renamed already.
  2. Creates `Esport_city_2` at the same Site if it does not exist.
  3. Moves the racks that belong to dist 2 into the new Location.
  4. Moves the dist 2 Device into the new Location.

Idempotent. Each step checks current state before acting.

Usage,
    export NB_TOKEN="..."
    ./netbox_split_esport_city.py            # dry run (default)
    ./netbox_split_esport_city.py --apply
"""

from __future__ import annotations

import argparse
import sys

from netbox_common import NetboxClient, require_token

# The dist 2 table list at the time of the split. Hard coded inline since
# the script is a one shot historical record, the live source of truth is
# now NetBox itself through `dist_tables_for`.
DIST_2_TABLES: list[tuple[int, int]] = [
    (8, 2), (9, 2), (10, 2), (11, 2),
    (12, 2), (13, 2), (14, 2), (15, 2),
]

OLD_NAME = "Esport_city"
NEW_NAME_1 = "Esport_city_1"
NEW_NAME_2 = "Esport_city_2"
NEW_SLUG_2 = "esport_city_2"

DIST_1 = "C-ESPORTS-CITY-1-SW"
DIST_2 = "C-ESPORTS-CITY-2-SW"


def find_location(client: NetboxClient, name: str) -> dict | None:
    matches = client.get_all(f"dcim/locations/?name={name}")
    return matches[0] if matches else None


def find_rack(client: NetboxClient, name: str) -> dict | None:
    matches = client.get_all(f"dcim/racks/?name={name}")
    return matches[0] if matches else None


def find_device(client: NetboxClient, name: str) -> dict | None:
    matches = client.get_all(f"dcim/devices/?name={name}")
    return matches[0] if matches else None


def step_rename(client: NetboxClient, apply: bool) -> dict | None:
    """Return the Location object that ends up holding `Esport_city_1`."""
    existing_new = find_location(client, NEW_NAME_1)
    if existing_new:
        print(f"  [ok]   Location {NEW_NAME_1!r} already exists "
              f"(id {existing_new['id']})")
        return existing_new
    existing_old = find_location(client, OLD_NAME)
    if not existing_old:
        print(f"  [MISS] Neither {OLD_NAME!r} nor {NEW_NAME_1!r} found, "
              f"manual investigation needed")
        return None
    if not apply:
        print(f"  [DRY]  would rename Location {OLD_NAME!r} to {NEW_NAME_1!r}")
        return existing_old
    patched = client.patch(f"dcim/locations/{existing_old['id']}/",
                           {"name": NEW_NAME_1})
    print(f"  [NEW]  renamed Location {OLD_NAME!r} to {NEW_NAME_1!r}")
    return patched


def step_create_new_location(client: NetboxClient, site_id: int,
                             apply: bool) -> dict | None:
    """Return the `Esport_city_2` Location, creating it if missing."""
    existing = find_location(client, NEW_NAME_2)
    if existing:
        print(f"  [ok]   Location {NEW_NAME_2!r} already exists "
              f"(id {existing['id']})")
        return existing
    if not apply:
        print(f"  [DRY]  would create Location {NEW_NAME_2!r} at site id {site_id}")
        return None
    created = client.post("dcim/locations/", {
        "name": NEW_NAME_2,
        "slug": NEW_SLUG_2,
        "site": site_id,
        "description": "Esports city, split from Esport_city to remove "
                       "the two dist ambiguity.",
    })
    print(f"  [NEW]  created Location {NEW_NAME_2!r} (id {created['id']})")
    return created


def step_move_racks(client: NetboxClient, dist_name: str,
                    new_location_id: int | None, apply: bool) -> int:
    """Move the racks that belong to dist 2 into the new Location."""
    if dist_name != DIST_2:
        print(f"  [MISS] this script only knows how to move racks for {DIST_2!r}")
        return 1
    table_list = DIST_2_TABLES
    hall = dist_name[0]
    moved = 0
    failures = 0
    for table_num, _count in table_list:
        rack_name = f"{hall}{table_num}"
        rack = find_rack(client, rack_name)
        if not rack:
            print(f"  [MISS] rack {rack_name!r} not found")
            failures += 1
            continue
        current_loc = (rack.get("location") or {}).get("id")
        if current_loc == new_location_id:
            print(f"  [ok]   rack {rack_name!r} already in target Location")
            continue
        if not apply:
            print(f"  [DRY]  would move rack {rack_name!r} to {NEW_NAME_2!r}")
            moved += 1
            continue
        client.patch(f"dcim/racks/{rack['id']}/",
                     {"location": new_location_id})
        print(f"  [NEW]  moved rack {rack_name!r} to {NEW_NAME_2!r}")
        moved += 1
    print(f"  ({moved} rack(s) moved, {failures} failures)")
    return failures


def step_move_dist_rack(client: NetboxClient, dist_name: str,
                        new_location_id: int | None, apply: bool) -> int:
    """
    NetBox requires device.rack.location to match device.location. The
    dist device sits in its own rack (named `<District>_Dist`), so the
    rack has to move before the device.
    """
    dist = find_device(client, dist_name)
    if not dist:
        print(f"  [MISS] device {dist_name!r} not found")
        return 1
    rack_ref = dist.get("rack")
    if not rack_ref:
        print(f"  [ok]   {dist_name!r} has no rack assignment, nothing to move")
        return 0
    rack_id = rack_ref.get("id")
    rack_full = client.get_one(f"dcim/racks/{rack_id}/")
    if not rack_full:
        print(f"  [MISS] rack id {rack_id} could not be fetched")
        return 1
    rack_name = rack_full.get("name", f"id={rack_id}")
    current_loc = (rack_full.get("location") or {}).get("id")
    if current_loc == new_location_id:
        print(f"  [ok]   rack {rack_name!r} already in target Location")
        return 0
    if not apply:
        print(f"  [DRY]  would move rack {rack_name!r} to {NEW_NAME_2!r}")
        return 0
    client.patch(f"dcim/racks/{rack_id}/", {"location": new_location_id})
    print(f"  [NEW]  moved rack {rack_name!r} to {NEW_NAME_2!r}")
    return 0


def step_move_dist(client: NetboxClient, dist_name: str,
                   new_location_id: int | None, apply: bool) -> int:
    dist = find_device(client, dist_name)
    if not dist:
        print(f"  [MISS] device {dist_name!r} not found")
        return 1
    current_loc = (dist.get("location") or {}).get("id")
    if current_loc == new_location_id:
        print(f"  [ok]   {dist_name!r} already in target Location")
        return 0
    if not apply:
        print(f"  [DRY]  would move device {dist_name!r} to {NEW_NAME_2!r}")
        return 0
    client.patch(f"dcim/devices/{dist['id']}/",
                 {"location": new_location_id})
    print(f"  [NEW]  moved device {dist_name!r} to {NEW_NAME_2!r}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Apply changes. Default is dry run.")
    args = parser.parse_args()

    if not require_token():
        return 1
    client = NetboxClient()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode, {mode}")

    print()
    print("Step 1, rename existing Location")
    loc1 = step_rename(client, args.apply)
    if loc1 is None:
        return 2
    site_id = (loc1.get("site") or {}).get("id")
    if site_id is None:
        print(f"  [MISS] Location {NEW_NAME_1!r} has no site, cannot create "
              f"sibling {NEW_NAME_2!r}")
        return 2

    print()
    print("Step 2, create new Location")
    loc2 = step_create_new_location(client, site_id, args.apply)
    new_loc_id = loc2["id"] if loc2 else None
    if not args.apply and new_loc_id is None:
        # During dry run the new Location does not exist yet, the rack
        # and device steps still preview their target name.
        new_loc_id = None

    print()
    print("Step 3, move dist 2 table racks into the new Location")
    failures = step_move_racks(client, DIST_2, new_loc_id, args.apply)

    print()
    print("Step 4, move dist 2 own rack into the new Location")
    failures += step_move_dist_rack(client, DIST_2, new_loc_id, args.apply)

    print()
    print("Step 5, move dist 2 device into the new Location")
    failures += step_move_dist(client, DIST_2, new_loc_id, args.apply)

    print()
    print("=" * 60)
    if not args.apply:
        print("  Dry run only. Re run with --apply to make the changes.")
    elif failures:
        print(f"  Completed with {failures} issue(s) to inspect.")
    else:
        print("  Split complete.")
    print("=" * 60)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
