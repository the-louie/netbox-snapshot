#!/usr/bin/env python3
"""
Move the `district_token` value out of `netbox_common.py` and into NetBox.

The token is the string the Junos renderer uses to name the per district
mgmt VLAN (`<token>_ACCESS-MGMT`) and the irb.600 description. It is
operator visible, irregular for the two `ESPORTS_CITY-*` dists, and best
stored alongside the dist Device.

The script,
  1. Creates a Custom Field `district_token` on `dcim.device` if it does
     not exist already, type text, no choice list.
  2. Sets the value on each known dist Device from the canonical map.

Idempotent. The field is left alone if its shape already matches, values
are only written when they differ.

Usage,
    export NB_TOKEN="..."
    ./netbox_fill_district_token.py            # dry run (default)
    ./netbox_fill_district_token.py --apply
"""

from __future__ import annotations

import argparse
import sys

from netbox_common import NetboxClient, require_token

# Source of truth at the time this fill was performed. The values now live
# in NetBox under the `district_token` custom field on each dist Device,
# the dict is retained inline so the script remains a runnable historical
# record without depending on a long retired module level constant.
DIST_TOKENS: dict[str, str] = {
    "D-THE-FORGE-SW":      "THE_FORGE",
    "D-MIRAGE-PALACE-SW":  "MIRAGE_PALACE",
    "D-Neon-District-SW":  "NEON_DISTRICT",
    "D-Pink-Heli-SW":      "PINK_HELI",
    "D-Tokyo-Town-SW":     "TOKYO_TOWN",
    "D-Tilted-Blocks-SW":  "TILTED_BLOCKS",
    "C-Chill-Avenue-SW":   "CHILL_AVENUE",
    "C-ESPORTS-CITY-1-SW": "ESPORTS_CITY-1",
    "C-ESPORTS-CITY-2-SW": "ESPORTS_CITY-2",
}

FIELD_NAME = "district_token"
FIELD_LABEL = "District Token"
FIELD_DESCRIPTION = (
    "Token used by the Junos renderer for VLAN names "
    "(<token>_ACCESS-MGMT) and the irb.600 description. "
    "Uppercase, underscores for spaces, irregular for ESPORTS_CITY-1 / -2."
)
CONTENT_TYPE = "dcim.device"


def find_field(client: NetboxClient) -> dict | None:
    matches = client.get_all(f"extras/custom-fields/?name={FIELD_NAME}")
    return matches[0] if matches else None


def find_device(client: NetboxClient, name: str) -> dict | None:
    matches = client.get_all(f"dcim/devices/?name={name}")
    return matches[0] if matches else None


def ensure_field(client: NetboxClient, apply: bool) -> tuple[str, dict | None]:
    """Create the custom field if absent. Returns (status, field_object)."""
    existing = find_field(client)
    if existing:
        types = existing.get("object_types") or existing.get("content_types") or []
        if CONTENT_TYPE in {str(t) for t in types}:
            return "exists", existing
        # The field exists but on the wrong content type. Surface this
        # rather than silently retrying, the operator decides whether to
        # delete or rename the existing field.
        return "wrong_type", existing
    if not apply:
        return "dry_create", None
    body = {
        "name": FIELD_NAME,
        "label": FIELD_LABEL,
        "description": FIELD_DESCRIPTION,
        "type": "text",
        "object_types": [CONTENT_TYPE],
        "required": False,
    }
    created = client.post("extras/custom-fields/", body)
    return "created", created


def ensure_value(client: NetboxClient, dist_name: str, token: str,
                 apply: bool) -> str:
    """Set district_token on a dist. Returns a status string."""
    device = find_device(client, dist_name)
    if not device:
        return "missing_device"
    current = (device.get("custom_fields") or {}).get(FIELD_NAME)
    if current == token:
        return "exists"
    if not apply:
        return "dry_assign"
    client.patch(f"dcim/devices/{device['id']}/",
                 {"custom_fields": {FIELD_NAME: token}})
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
    print("Step 2, write district_token on every known dist")
    failures = 0
    for dist_name, token in DIST_TOKENS.items():
        status = ensure_value(client, dist_name, token, args.apply)
        print(f"  [{status:<12}] {dist_name:<22} district_token = {token!r}")
        if status == "missing_device":
            failures += 1

    print()
    print("=" * 60)
    if not args.apply:
        print("  Dry run only. Re run with --apply.")
    elif failures:
        print(f"  Completed with {failures} issue(s) to inspect.")
    else:
        print("  district_token now lives in NetBox.")
    print("=" * 60)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
