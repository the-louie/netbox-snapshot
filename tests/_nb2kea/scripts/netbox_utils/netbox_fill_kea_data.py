#!/usr/bin/env python3
"""
Populate the NetBox structure that lets `netbox2kea.py` resolve every
constant through NetBox lookups instead of script side hard coding.

This is a one shot, idempotent change. The script,

  1. Creates two Prefix Roles, `kea-bootstrap` and `kea-crew`, if they do
     not already exist.
  2. Assigns the matching Role to each known Prefix.
  3. Creates an IP Range inside each Prefix for the DHCP dynamic pool,
     carrying the same Role so the renderer can find pool and Prefix
     through the same selector.

No IP Address objects are touched. The existing `dns_name` field on each
service IP (ns01, ns02, dhcp01, dhcp02, tftp) is what the renderer will
match against, no relabeling is needed there.

Usage,
    export NB_TOKEN="..."
    ./netbox_fill_kea_data.py            # dry run (default)
    ./netbox_fill_kea_data.py --apply    # actually create or assign
"""

from __future__ import annotations

import argparse
import sys

from netbox_common import NetboxClient, require_token

# The role definitions to ensure exist in NetBox. The slug is the durable
# selector the renderer queries by, the name is the human label.
KEA_ROLES = [
    {
        "slug": "kea-bootstrap",
        "name": "Kea Bootstrap",
        "description": "Bootstrap phase 1 DHCP subnet and pool for access switches.",
    },
    {
        "slug": "kea-crew",
        "name": "Kea Crew",
        "description": "INFRA-CREW DHCP subnet and pool for crew laptops.",
    },
]

# Existing prefixes that should be tagged with a role, looked up by their
# exact CIDR. Each entry pairs a prefix with the role slug to apply and the
# IP Range that defines the DHCP dynamic pool inside it.
PREFIX_AND_POOL = [
    {
        "prefix":      "92.33.43.192/26",
        "role_slug":   "kea-bootstrap",
        "pool_start":  "92.33.43.198/26",
        "pool_end":    "92.33.43.254/26",
        "pool_desc":   "Kea DHCP bootstrap pool, phase 1 leases for access switches",
    },
    {
        "prefix":      "92.33.58.96/27",
        "role_slug":   "kea-crew",
        "pool_start":  "92.33.58.105/27",
        "pool_end":    "92.33.58.126/27",
        "pool_desc":   "Kea DHCP crew pool, INFRA-CREW dynamic leases",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_role(client: NetboxClient, slug: str) -> dict | None:
    """Return the Role object with this slug or None."""
    matches = client.get_all(f"ipam/roles/?slug={slug}")
    return matches[0] if matches else None


def find_prefix(client: NetboxClient, cidr: str) -> dict | None:
    """Return the Prefix object matching this exact CIDR or None."""
    matches = client.get_all(f"ipam/prefixes/?prefix={cidr}")
    return matches[0] if matches else None


def find_ip_range(client: NetboxClient, start: str, end: str) -> dict | None:
    """
    Return the IP Range whose start_address and end_address match, or None.
    NetBox stores addresses with a prefix length, the caller passes the
    same notation back so the comparison is exact.
    """
    matches = client.get_all(
        f"ipam/ip-ranges/?start_address={start}&end_address={end}"
    )
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def ensure_role(client: NetboxClient, role_def: dict,
                apply: bool) -> tuple[dict | None, str]:
    """
    Ensure a Prefix Role with this slug exists. Returns the role object
    (or a placeholder for dry runs) and a status string.
    """
    existing = find_role(client, role_def["slug"])
    if existing:
        return existing, "exists"
    if not apply:
        return None, "dry_create"
    created = client.post("ipam/roles/", {
        "slug": role_def["slug"],
        "name": role_def["name"],
        "description": role_def.get("description", ""),
    })
    return created, "created"


def ensure_prefix_role(client: NetboxClient, prefix_obj: dict,
                       role_obj: dict | None, apply: bool) -> str:
    """
    Assign the role to the prefix. Skips the assignment when the prefix
    already carries the same role. Returns a status string.
    """
    current_role_slug = (prefix_obj.get("role") or {}).get("slug")
    target_slug = role_obj["slug"] if role_obj else None
    if current_role_slug == target_slug:
        return "exists"
    if not apply:
        return "dry_assign"
    client.patch(f"ipam/prefixes/{prefix_obj['id']}/",
                 {"role": role_obj["id"]})
    return "assigned"


def ensure_ip_range(client: NetboxClient, entry: dict,
                    role_obj: dict | None, apply: bool) -> tuple[str, dict | None]:
    """
    Ensure an IP Range with the given start, end, role, and description
    exists inside the prefix. Returns (status, object_or_None).
    """
    existing = find_ip_range(client, entry["pool_start"], entry["pool_end"])
    if existing:
        # Range is there, check if the role matches.
        current = (existing.get("role") or {}).get("slug")
        target = role_obj["slug"] if role_obj else None
        if current == target:
            return "exists", existing
        if not apply:
            return "dry_assign", existing
        patched = client.patch(f"ipam/ip-ranges/{existing['id']}/",
                               {"role": role_obj["id"]})
        return "assigned", patched
    if not apply:
        return "dry_create", None
    created = client.post("ipam/ip-ranges/", {
        "start_address": entry["pool_start"],
        "end_address":   entry["pool_end"],
        "description":   entry["pool_desc"],
        "status":        "active",
        "role":          role_obj["id"] if role_obj else None,
    })
    return "created", created


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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

    # Step 1, create the two Prefix Roles.
    print("== Roles ==")
    role_by_slug: dict[str, dict | None] = {}
    for role_def in KEA_ROLES:
        obj, status = ensure_role(client, role_def, args.apply)
        role_by_slug[role_def["slug"]] = obj
        print(f"  [{status:<12}] role slug={role_def['slug']}")

    # Step 2, assign roles to existing prefixes, plus create the pool ranges.
    print()
    print("== Prefixes and pools ==")
    failures = 0
    for entry in PREFIX_AND_POOL:
        cidr = entry["prefix"]
        role_obj = role_by_slug[entry["role_slug"]]
        prefix_obj = find_prefix(client, cidr)
        if not prefix_obj:
            print(f"  [MISS]  prefix {cidr} does not exist in IPAM, cannot tag")
            failures += 1
            continue

        status = ensure_prefix_role(client, prefix_obj, role_obj, args.apply)
        print(f"  [{status:<12}] prefix {cidr} role={entry['role_slug']}")

        range_status, _ = ensure_ip_range(client, entry, role_obj, args.apply)
        print(f"  [{range_status:<12}] ip-range {entry['pool_start']} .. "
              f"{entry['pool_end']} role={entry['role_slug']}")

    print()
    print("=" * 60)
    if not args.apply:
        print("  Dry run only. Re run with --apply to make the changes.")
    elif failures:
        print(f"  Completed with {failures} missing prefix(es).")
    else:
        print("  Completed cleanly.")
    print("=" * 60)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
