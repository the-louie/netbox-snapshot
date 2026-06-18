#!/usr/bin/env python3
"""
Create the access switch to dist switch cables in NetBox, and set each
dist port's description so the Junos `relay-option-82 circuit-id
use-interface-description device` will emit the right Option 82 circuit
id.

For each dist, the table list is read from NetBox through `dist_tables_for`,
which walks the racks at the dist's Location and reads `switch_count` on
each. At index N the script wires,
    access  <hall><table:02d><slot>  .Gi0/2
    dist    <dist>                   .ge-0/0/N    desc 'TABLE; <hall><table:02d>-<slot>'

The cable type defaults to cat6 (1G copper), override with --cable-type.

Idempotent,
  - If either end is already cabled, fetch the cable and verify both
    terminations match the expected (access Gi0/2, dist ge-0/0/N)
    pair. A matching cable counts as already-done. A mismatched
    cable is flagged as a CONFLICT and the script does not touch it,
    the operator must fix the physical wiring or remove the NetBox
    cable before re running.
  - Skips description update if the dist port already carries the
    expected text.

Usage,
    export NB_TOKEN="..."
    ./netbox_create_cables.py                          # dry run (default)
    ./netbox_create_cables.py --apply
    ./netbox_create_cables.py --dist D-THE-FORGE-SW --apply
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
    port_description,
    require_token,
)

# Access uplink interface on a WS-C2950T-24, by NetBox device type library
# naming. The script tries the short form as a fallback in case the
# imported device type template uses the abbreviated 'Gi0/2'.
ACCESS_UPLINK_NAMES = ("GigabitEthernet0/2", "Gi0/2")

DEFAULT_CABLE_TYPE = "cat6"
CABLE_STATUS = "connected"


def find_access_uplink(ifaces: list[dict]) -> dict | None:
    """Locate the access switch's uplink interface in either name form."""
    by_name = {i["name"]: i for i in ifaces}
    for name in ACCESS_UPLINK_NAMES:
        if name in by_name:
            return by_name[name]
    return None


def verify_cable_peers(client: NetboxClient,
                       cable_brief: dict,
                       expected_access_iface_id: int,
                       expected_dist_iface_id: int) -> tuple[bool, str]:
    """
    Fetch the cable and check that the set of terminated interface ids
    matches the expected (access uplink, dist port) pair. Returns
    `(ok, detail)`. `ok=False` is the misconnection case, `detail`
    names the actual termination set so the operator can fix the
    cable without reopening NetBox.

    The check intentionally compares unordered sets, NetBox allows the
    operator to enter either end as the A side or the B side, both
    orientations describe the same physical cable.

    `cable_brief` is the embedded cable summary NetBox returns on an
    interface. Its `id` field is enough to fetch the full cable object.
    """
    cable_id = cable_brief.get("id") if isinstance(cable_brief, dict) else None
    if cable_id is None:
        # The interface claims to be cabled but the embedded summary
        # is shaped unexpectedly. Treat this as a verification miss
        # so the operator gets a clear message rather than a silent
        # acceptance.
        return False, "interface reports a cable with no id"
    cable = client.get_one(f"dcim/cables/{cable_id}/")
    if cable is None:
        return False, f"cable id {cable_id} not retrievable"

    def iface_ids(terms: list[dict] | None) -> set[int]:
        # NetBox cable terminations can in principle target other object
        # types (front ports, rear ports, circuit terminations). The
        # 2026 fleet is interface to interface, anything else is a data
        # error that the operator must fix.
        ids: set[int] = set()
        for t in terms or []:
            if t.get("object_type") == "dcim.interface":
                ids.add(t["object_id"])
        return ids

    actual = iface_ids(cable.get("a_terminations")) | iface_ids(
        cable.get("b_terminations"))
    expected = {expected_access_iface_id, expected_dist_iface_id}
    if actual == expected:
        return True, ""
    return False, (
        f"cable {cable_id} terminates interface ids {sorted(actual)}, "
        f"expected {sorted(expected)}"
    )


def process_dist(client: NetboxClient,
                 dist: dict,
                 devices_by_name: dict[str, dict],
                 cable_type: str,
                 apply: bool) -> tuple[int, int, int]:
    name = dist["name"]
    try:
        tables = dist_tables_for(client, name)
    except RuntimeError as exc:
        print(f"\n=== {name}, [SKIP] {exc}")
        return 0, 0, 1
    if not tables:
        print(f"\n=== {name}, [SKIP] no participant tables resolved from NetBox")
        return 0, 0, 1

    print(f"\n=== {name} ===")
    hall = name[0]
    created = skipped = failed = 0

    dist_ifaces = client.get_all(f"dcim/interfaces/?device_id={dist['id']}")
    dist_by_name = {i["name"]: i for i in dist_ifaces}

    # The index doubles as the dist port number, the first access switch
    # in ROWS order lands on ge-0/0/0, the second on ge-0/0/1, and so on.
    index = 0
    for table_num, count in tables:
        slots = ["A"] if count == 1 else ["A", "B"]
        for slot in slots:
            hostname = make_access_hostname(hall, table_num, slot)
            desc = port_description(hall, table_num, slot)
            dist_port_name = f"ge-0/0/{index}"
            index += 1

            access = devices_by_name.get(hostname)
            if access is None:
                print(f"  [MISS] {hostname:<6} not in NetBox, "
                      f"run netbox_create_access_switches.py first")
                failed += 1
                continue

            dist_port = dist_by_name.get(dist_port_name)
            if dist_port is None:
                print(f"  [MISS] dist port {dist_port_name} not found on {name}")
                failed += 1
                continue

            access_ifaces = client.get_all(
                f"dcim/interfaces/?device_id={access['id']}"
            )
            access_uplink = find_access_uplink(access_ifaces)
            if access_uplink is None:
                print(f"  [MISS] {hostname:<6} has no GigabitEthernet0/2 or Gi0/2")
                failed += 1
                continue

            cable_label = f"{hostname:<6} Gi0/2 <-> {dist_port_name}"

            access_cable = access_uplink.get("cable")
            dist_cable = dist_port.get("cable")
            if access_cable or dist_cable:
                # An interface that already carries a cable could be in
                # one of three states, identical to the one this script
                # would create, miscabled to the wrong peer, or in a
                # half configured state where only one end is cabled.
                # Trust nothing, fetch the cable and confirm both ends.
                #
                # The check uses whichever embedded cable summary is
                # available, NetBox emits both when both ends are
                # cabled to the same cable.
                cable_brief = access_cable or dist_cable
                ok, detail = verify_cable_peers(
                    client, cable_brief,
                    expected_access_iface_id=access_uplink["id"],
                    expected_dist_iface_id=dist_port["id"],
                )
                if ok:
                    print(f"  [ok]   {cable_label}  already cabled")
                    skipped += 1
                else:
                    # A misconnection is a hard failure, the operator
                    # must either fix the physical cable or remove the
                    # NetBox cable before the script can recreate it.
                    print(f"  [CONFLICT] {cable_label}  {detail}",
                          file=sys.stderr)
                    failed += 1
            elif not apply:
                print(f"  [DRY]  {cable_label}  would create cable ({cable_type})")
                created += 1
            else:
                try:
                    cable = client.post("dcim/cables/", {
                        "a_terminations": [
                            {"object_type": "dcim.interface",
                             "object_id": access_uplink["id"]}
                        ],
                        "b_terminations": [
                            {"object_type": "dcim.interface",
                             "object_id": dist_port["id"]}
                        ],
                        "type": cable_type,
                        "status": CABLE_STATUS,
                    })
                    print(f"  [NEW]  {cable_label}  cable id {cable['id']}")
                    created += 1
                except RuntimeError as exc:
                    print(f"  [FAIL] {cable_label}  {exc}")
                    failed += 1

            current_desc = dist_port.get("description") or ""
            if current_desc == desc:
                print(f"  [ok]   {dist_port_name:<10} description already '{desc}'")
                skipped += 1
            elif not apply:
                if current_desc:
                    print(f"  [DRY]  {dist_port_name:<10} would change description "
                          f"'{current_desc}' to '{desc}'")
                else:
                    print(f"  [DRY]  {dist_port_name:<10} would set description '{desc}'")
                created += 1
            else:
                try:
                    client.patch(f"dcim/interfaces/{dist_port['id']}/",
                                 {"description": desc})
                    print(f"  [NEW]  {dist_port_name:<10} description set '{desc}'")
                    created += 1
                except RuntimeError as exc:
                    print(f"  [FAIL] {dist_port_name:<10} description, {exc}")
                    failed += 1

    return created, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually POST or PATCH. Default is dry run.")
    parser.add_argument("--dist", metavar="NAME",
                        help="Only process this dist.")
    parser.add_argument("--cable-type", default=DEFAULT_CABLE_TYPE,
                        help=f"Cable type slug (default, {DEFAULT_CABLE_TYPE}).")
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()
    print(f"Mode, {'APPLY' if args.apply else 'DRY-RUN'}  "
          f"cable-type={args.cable_type}")

    dists_all = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
    dists_by_name = {d["name"]: d for d in dists_all}

    access_all = client.get_all(f"dcim/devices/?role={ROLE_ACCESS}")
    access_by_name = {d["name"]: d for d in access_all}
    print(f"Found {len(dists_by_name)} dists, "
          f"{len(access_by_name)} access switches")

    if args.dist:
        if args.dist not in dists_by_name:
            print(f"Error, dist {args.dist!r} not found.", file=sys.stderr)
            return 1
        target_dists = [dists_by_name[args.dist]]
    else:
        # All known dists, sorted by name for stable output ordering.
        target_dists = [dists_by_name[n] for n in sorted(dists_by_name)]

    totals = [0, 0, 0]
    for dist in target_dists:
        try:
            c, s, f = process_dist(client, dist, access_by_name,
                                    args.cable_type, args.apply)
            totals[0] += c
            totals[1] += s
            totals[2] += f
        except RuntimeError as exc:
            print(f"\n[FATAL] {dist['name']}, {exc}", file=sys.stderr)
            totals[2] += 1

    print()
    print("=" * 60)
    print(f"  {'Created/changed' if args.apply else 'Would create/change'}, "
          f"{totals[0]}")
    print(f"  Already in place,                  {totals[1]}")
    print(f"  Failed or skipped,                 {totals[2]}")
    print("=" * 60)

    if not args.apply:
        print("\nDry run complete. Re run with --apply to actually create cables.")

    return 0 if totals[2] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
