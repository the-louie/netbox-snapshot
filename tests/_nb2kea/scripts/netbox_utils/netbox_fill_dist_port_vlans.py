#!/usr/bin/env python3
"""
Backfill `mode` and `untagged_vlan` on every cabled `ge-0/0/N` dist port.

Background, the d46af24 strict fail migration of `netbox2junos.py` made
the dist port's `untagged_vlan` binding load bearing, the renderer now
reads the participant or crew VID from that field instead of parsing
it out of the port description. The API driven NetBox bootstrap that
populated the fleet from the 2025 dist configs only set the
description (because that is what the legacy configs carry), the
untagged_vlan field was never written. This script closes the gap.

For each cabled `ge-0/0/N` port on every device with role
`distribution_switches`,

  1. Read the description.
  2. If the shape is `TABLE; <hall><NN>-<slot>`, derive the participant
     VID via `participant_vlan_for_table`. Hall D adds 200, hall C
     adds 100, see `HALL_VLAN_BASE`.
  3. If the shape is `CREW; ...`, use the single crew VID 199
     (today the only crew network in the fleet, D-INFRA-SW serves it
     on `irb.199`).
  4. Any other shape is reported as `[skip]`, the operator decides
     what to do.
  5. Verify the target VLAN object exists in NetBox, refuse to PATCH
     against a non existent VLAN.
  6. PATCH the interface with `mode=access` and
     `untagged_vlan=<vlan id>`. Idempotent, a port that is already
     correctly configured prints `[ok]` and is skipped. A port whose
     current `untagged_vlan` disagrees with the derived VID prints
     `[CONFLICT]` and is left untouched, the operator resolves it.

Default is dry run, `--apply` is required for any PATCH. `--dist NAME`
limits the run to a single dist. Exit code is 2 when any
`[CONFLICT]` or `[FAIL]` line appears, 0 otherwise. The convention
matches the other `netbox_fill_*` and `netbox_create_*` bootstrap
scripts in this directory.

Usage,
    export NB_TOKEN="..."
    ./netbox_fill_dist_port_vlans.py                  # dry run, all dists
    ./netbox_fill_dist_port_vlans.py --apply          # commit changes
    ./netbox_fill_dist_port_vlans.py --dist D-THE-FORGE-SW --apply
"""

from __future__ import annotations

import argparse
import re
import sys

from netbox_common import (
    NetboxClient,
    ROLE_DIST,
    participant_vlan_for_table,
    require_token,
    vlans_by_vid,
)

# Cabled `ge-0/0/N` ports only. Other PIC positions, other speeds,
# and uncabled ports are not in scope, the renderer only emits
# PHYSICAL-INTERFACES entries for `ge-0/0/N` cabled ports today.
GE_RE = re.compile(r"^ge-0/0/(\d+)$")

# Description shapes the script recognises. The TABLE shape encodes
# the hall + table id + slot letter, the CREW shape is the only non
# table descriptor in the fleet today. Both follow the operator
# convention documented in `reference_documentation/howto/add-a-dist.md`
# section 9.
TABLE_RE = re.compile(r"^TABLE;\s*([A-Z])(\d+)-([A-Z])$")
CREW_VID = 199


def derive_vid(description: str) -> int | None:
    """
    Map a port description to its target VID, or None when the shape
    is not recognised. The caller treats None as `[skip]`.
    """
    m = TABLE_RE.match(description)
    if m:
        hall, table_str, _slot = m.group(1), m.group(2), m.group(3)
        return participant_vlan_for_table(hall, int(table_str))
    if description.startswith("CREW;"):
        return CREW_VID
    return None


def process_port(client: NetboxClient, iface: dict,
                 vid_to_vlan_id: dict[int, int], apply: bool) -> str:
    """
    Resolve the target VID for one cabled `ge-0/0/N` port and either
    PATCH the interface or report what would change. Returns the
    status tag the main loop accumulates.
    """
    desc = (iface.get("description") or "").strip()
    if not desc:
        return "[skip-no-desc]"

    try:
        vid = derive_vid(desc)
    except KeyError as exc:
        # Hall not in HALL_VLAN_BASE, an explicit data error in the
        # description that the operator must resolve in NetBox.
        return f"[FAIL-{exc}]"
    if vid is None:
        return f"[skip-shape] {desc!r}"

    vlan_id = vid_to_vlan_id.get(vid)
    if vlan_id is None:
        return f"[FAIL-no-vlan] VID {vid}"

    current_mode = (iface.get("mode") or {}).get("value")
    current_vid = (iface.get("untagged_vlan") or {}).get("vid")

    if current_vid is not None and current_vid != vid:
        return (f"[CONFLICT] current untagged_vlan {current_vid}, "
                f"derived {vid}")

    if current_mode == "access" and current_vid == vid:
        return "[ok]"

    if not apply:
        return f"[dry_patch] mode=access, untagged_vlan={vid}"

    client.patch(f"dcim/interfaces/{iface['id']}/", {
        "mode": "access",
        "untagged_vlan": vlan_id,
    })
    return f"[patched] mode=access, untagged_vlan={vid}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually PATCH. Default is dry run.")
    parser.add_argument("--dist", metavar="NAME",
                        help="Limit the run to a single dist by Device.name.")
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()

    # Build a flat vid → vlan_id index. Site scope is irrelevant for
    # the PATCH itself, NetBox accepts the global VLAN id and the
    # interface inherits it. The renderer's site preference is a
    # separate concern handled at render time by `lookup_vlan`.
    vlan_index = vlans_by_vid(client)
    vid_to_vlan_id: dict[int, int] = {}
    for (_site_id, vid), vlan in vlan_index.items():
        vid_to_vlan_id.setdefault(vid, vlan["id"])

    dists = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
    if args.dist:
        dists = [d for d in dists if d["name"] == args.dist]
        if not dists:
            print(f"Error, dist {args.dist!r} not found.", file=sys.stderr)
            return 1

    print(f"Found {len(dists)} dist device(s), "
          f"{len(vid_to_vlan_id)} unique VLAN ids in NetBox")

    counts: dict[str, int] = {}
    conflicts: list[tuple[str, str, str]] = []
    fails: list[tuple[str, str, str]] = []

    for dist in sorted(dists, key=lambda d: d["name"]):
        ifs = client.get_all(f"dcim/interfaces/?device_id={dist['id']}")
        cabled = [i for i in ifs if GE_RE.match(i["name"]) and i.get("cable")]
        cabled.sort(key=lambda i: int(GE_RE.match(i["name"]).group(1)))
        if not cabled:
            continue
        print(f"\n{dist['name']}  ({len(cabled)} cabled ge ports)")
        for iface in cabled:
            status = process_port(client, iface, vid_to_vlan_id, args.apply)
            # Bucket on the tag prefix so the counts summary stays
            # readable, the per port detail line keeps the full text.
            bucket = status.split(" ")[0].split("-")[0]
            counts[bucket] = counts.get(bucket, 0) + 1
            print(f"  {iface['name']:10s} {status}")
            if status.startswith("[CONFLICT]"):
                conflicts.append((dist["name"], iface["name"], status))
            if status.startswith("[FAIL"):
                fails.append((dist["name"], iface["name"], status))

    print()
    print("=" * 60)
    for bucket, n in sorted(counts.items()):
        print(f"  {bucket:<14} {n}")
    print("=" * 60)

    if conflicts:
        print("\nConflicts, operator must resolve in NetBox:")
        for d, p, s in conflicts:
            print(f"  {d} {p} {s}")
    if fails:
        print("\nFailures:")
        for d, p, s in fails:
            print(f"  {d} {p} {s}")

    if not args.apply:
        print("\nDry run complete. Re run with --apply to commit the changes.")

    return 2 if (conflicts or fails) else 0


if __name__ == "__main__":
    sys.exit(main())
