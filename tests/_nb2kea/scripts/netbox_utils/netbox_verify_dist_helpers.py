#!/usr/bin/env python3
"""
Smoke test for the dist data helpers in `netbox_common.py`.

Calls `dist_info_for` and `dist_tables_for` on every dist Device and
prints the result. Used as a quick visual check that NetBox carries the
right values for each dist, the script never asserts anything, the
operator reads the output and confirms it looks correct.

Usage,
    export NB_TOKEN="..."
    ./netbox_verify_dist_helpers.py
"""

from __future__ import annotations

import sys

from netbox_common import (
    NetboxClient,
    ROLE_DIST,
    dist_info_for,
    dist_tables_for,
    require_token,
)


def main() -> int:
    if not require_token():
        return 1
    client = NetboxClient()
    devices = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
    if not devices:
        print("No dist devices found.")
        return 0

    issues = 0
    for device in sorted(devices, key=lambda d: d["name"]):
        name = device["name"]
        print(f"=== {name} ===")
        try:
            info = dist_info_for(client, name)
            print(f"  district_token = {info['district_token']!r}")
            print(f"  slug           = {info['slug']!r}")
            print(f"  mgmt_v4        = {info['mgmt_v4']!r}")
            print(f"  loopback_octet = {info['loopback_octet']}")
            print(f"  subnet_id      = {info['subnet_id']}")
        except RuntimeError as exc:
            print(f"  [FAIL] dist_info_for, {exc}")
            issues += 1
        try:
            tables = dist_tables_for(client, name)
            preview = ", ".join(f"({t},{c})" for t, c in tables[:6])
            tail = " ..." if len(tables) > 6 else ""
            print(f"  tables ({len(tables)}): {preview}{tail}")
        except RuntimeError as exc:
            print(f"  [FAIL] dist_tables_for, {exc}")
            issues += 1
        print()

    print("=" * 60)
    if issues:
        print(f"  {issues} helper call(s) failed, inspect [FAIL] lines above.")
    else:
        print("  All helper calls returned cleanly.")
    print("=" * 60)
    return 0 if issues == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
