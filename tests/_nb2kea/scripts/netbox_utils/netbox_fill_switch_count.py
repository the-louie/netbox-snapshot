#!/usr/bin/env python3
"""
Move the per table switch count from `DIST_TABLES` in `netbox_common.py`
into NetBox.

Each participant Rack carries one or two access switches. That number is
the only piece of the dist plan that NetBox does not already record (the
dist to rack relationship is given by the dist's Location, the rack
ordering follows the natural numeric sort of the rack name).

The script,
  1. Creates a Custom Field `switch_count` on `dcim.rack` if it does not
     exist already, integer type, with description naming the valid range.
  2. Sets the value on every rack named by `DIST_TABLES`, using the count
     from the (table, count) tuple.

Idempotent. Field shape is left alone if it matches, values are only
written when they differ.

Usage,
    export NB_TOKEN="..."
    ./netbox_fill_switch_count.py            # dry run (default)
    ./netbox_fill_switch_count.py --apply
"""

from __future__ import annotations

import argparse
import sys

from netbox_common import NetboxClient, require_token

# Source of truth at the time this fill was performed. Each Rack now
# carries the value as the `switch_count` custom field, the dict is
# retained inline so the script remains a runnable historical record.
DIST_TABLES_AT_FILL_TIME: dict[str, list[tuple[int, int]]] = {
    "D-THE-FORGE-SW": [
        (39, 2), (40, 2),
        (41, 1), (42, 1), (43, 1),
        (44, 2), (45, 2),
        (46, 1), (47, 1),
    ],
    "D-MIRAGE-PALACE-SW": [
        (48, 1), (49, 1), (50, 1), (51, 1), (52, 1),
        (53, 2), (54, 2), (55, 2), (56, 2),
    ],
    "D-Neon-District-SW": [
        (21, 2), (22, 2), (23, 2), (24, 2),
        (25, 1), (26, 1), (27, 1), (28, 1), (29, 1),
    ],
    "D-Pink-Heli-SW": [
        (30, 1), (31, 1), (32, 1), (33, 1), (34, 1),
        (35, 2), (36, 2), (37, 2), (38, 2),
    ],
    "D-Tokyo-Town-SW": [
        (1, 2), (2, 2),
        (3, 1), (4, 1), (5, 1), (6, 1), (7, 1), (8, 1),
        (17, 2), (18, 2),
    ],
    "D-Tilted-Blocks-SW": [
        (9, 1), (10, 1), (11, 1), (12, 1), (13, 1), (14, 1),
        (15, 2), (16, 2),
        (19, 2), (20, 2),
    ],
    "C-Chill-Avenue-SW":   [(1, 2), (2, 2), (3, 2)],
    "C-ESPORTS-CITY-1-SW": [(4, 2), (5, 2), (6, 2), (7, 2)],
    "C-ESPORTS-CITY-2-SW": [(8, 2), (9, 2), (10, 2), (11, 2),
                            (12, 2), (13, 2), (14, 2), (15, 2)],
}

FIELD_NAME = "switch_count"
FIELD_LABEL = "Switch Count"
FIELD_DESCRIPTION = (
    "Number of access switches in this participant table rack. "
    "Valid values today are 1 or 2."
)
CONTENT_TYPE = "dcim.rack"


def find_field(client: NetboxClient) -> dict | None:
    matches = client.get_all(f"extras/custom-fields/?name={FIELD_NAME}")
    return matches[0] if matches else None


def find_rack(client: NetboxClient, name: str) -> dict | None:
    matches = client.get_all(f"dcim/racks/?name={name}")
    return matches[0] if matches else None


def ensure_field(client: NetboxClient, apply: bool) -> tuple[str, dict | None]:
    """Create the custom field if absent. Returns (status, field_object)."""
    existing = find_field(client)
    if existing:
        types = existing.get("object_types") or existing.get("content_types") or []
        if CONTENT_TYPE in {str(t) for t in types}:
            return "exists", existing
        return "wrong_type", existing
    if not apply:
        return "dry_create", None
    body = {
        "name": FIELD_NAME,
        "label": FIELD_LABEL,
        "description": FIELD_DESCRIPTION,
        "type": "integer",
        "object_types": [CONTENT_TYPE],
        "required": False,
        "validation_minimum": 1,
        "validation_maximum": 2,
    }
    created = client.post("extras/custom-fields/", body)
    return "created", created


def ensure_value(client: NetboxClient, rack_name: str, count: int,
                 apply: bool) -> str:
    """Set switch_count on a rack. Returns a status string."""
    rack = find_rack(client, rack_name)
    if not rack:
        return "missing_rack"
    current = (rack.get("custom_fields") or {}).get(FIELD_NAME)
    if current == count:
        return "exists"
    if not apply:
        return "dry_assign"
    client.patch(f"dcim/racks/{rack['id']}/",
                 {"custom_fields": {FIELD_NAME: count}})
    return "assigned"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Apply changes. Default is dry run.")
    args = parser.parse_args()

    if not require_token():
        return 1
    client = NetboxClient()
    print(f"Mode, {'APPLY' if args.apply else 'DRY-RUN'}")

    print()
    print("Step 1, ensure custom field exists")
    field_status, _field = ensure_field(client, args.apply)
    print(f"  [{field_status:<12}] field {FIELD_NAME!r} on {CONTENT_TYPE}")
    if field_status == "wrong_type":
        print("  field exists on a different content type, manual fix needed",
              file=sys.stderr)
        return 2

    print()
    print("Step 2, write switch_count on every participant rack")
    failures = 0
    total = 0
    for dist_name, tables in DIST_TABLES_AT_FILL_TIME.items():
        hall = dist_name[0]
        for table_num, count in tables:
            rack_name = f"{hall}{table_num}"
            status = ensure_value(client, rack_name, count, args.apply)
            print(f"  [{status:<12}] {rack_name:<6} switch_count = {count}  "
                  f"(served by {dist_name})")
            total += 1
            if status == "missing_rack":
                failures += 1

    print()
    print("=" * 60)
    if not args.apply:
        print(f"  Dry run only, {total} racks planned. Re run with --apply.")
    elif failures:
        print(f"  Completed with {failures} missing rack(s) to inspect.")
    else:
        print(f"  switch_count now lives in NetBox for {total} racks.")
    print("=" * 60)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
