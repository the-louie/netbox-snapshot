#!/usr/bin/env python3
"""
Rename participant VLANs in NetBox to the per-table convention `<hall>_<table>`.

Background, the renderer reads `VLAN.name` verbatim into the Junos
config (the `set groups VLANS vlans <name>` line, the participant
IRB description, and the `set groups DOWNLINKS vlans <name>
l3-interface ...` binding). The API driven NetBox bootstrap created
each participant VLAN with the auto generated name `VLAN<vid>` (e.g.
`VLAN239`), whereas the production convention is `<hall>_<table>`
(e.g. `D_39`, `C_1`), no zero pad. This script aligns the lab to the
convention so the rendered Junos config matches the operator's
mental model and the legacy fleet diffs cleanly.

What this script touches and what it does not,

  Touches: VLAN objects whose current name matches the auto generated
           pattern `^VLAN<vid>$`. The match is exact, the digit
           suffix must equal the vid, so a VLAN that an operator
           deliberately named (`INFRA-CREW`, `Access-MGMT`,
           `OSPF-DEFAULT-MX1`, `Forge_39`) is never overwritten. To
           rename a custom named participant VLAN the operator does
           it in the NetBox UI, the script's job is to clear the
           auto generated names.

  Does not touch:
    * Mgmt VLAN 600. The per dist mgmt name lives on a site scoped
      VLAN 600 object, see `add-a-dist.md` section 7a. Renaming the
      global object would not produce per dist names, the fix there
      is to create site scoped VLAN 600 objects.
    * OSPF linknet VLANs 1100, 1101, 1200, 1201. Operator preference.
    * Crew VLAN 199 (`INFRA-CREW`). Already a reasonable name.
    * Any VLAN whose current name is not `VLAN<vid>` exact match.

Target derivation, for each candidate VLAN,

  1. Iterate `HALL_VLAN_BASE` and find every hall where
     `0 < (vid - base) <= MAX_PARTICIPANT_TABLES_PER_HALL`. The
     window is bounded only to keep the script from classifying
     infrastructure or OSPF VIDs as participants in some far hall.
  2. If exactly one hall matches, the target is
     `f"{hall}_{vid - base}"`. If zero halls match, the VLAN is
     reported as `[warn-unhandled]` (the operator should rename it
     in the UI). If two halls match (a future overlap), the script
     refuses with `[CONFLICT-ambiguous-hall]` and does not PATCH.
  3. The shared `INFRASTRUCTURE_VIDS` from `netbox_common.py` is
     removed from consideration up front. That constant covers the
     mgmt SVI, the OSPF linknets, the platform internal IRBs, and
     the crew VID 199. Even if a future `VLAN199` auto name
     appeared, the script would not rename it.

Pre-flight validation, before any PATCH the script,

  * Builds the full list of proposed renames in memory.
  * Detects duplicate VLAN objects on the same `(site_id, vid)` and
     refuses to PATCH either, `[CONFLICT-duplicate-vid]`.
  * Detects target name collisions in the same `(site_id, group_id)`
     scope and refuses to PATCH the renamer, `[CONFLICT-name-taken]`.
  * Validates that every target is a legal Junos identifier
     (`assert_junos_identifier`).

Only after every candidate passes pre-flight does `--apply` issue any
PATCH. A dry run shows the full proposed plan, the operator's review
is the safety, no PATCH is issued in dry run mode.

Exit code is 2 when any `[CONFLICT]`, `[FAIL]`, or
`[warn-unhandled]` line appears, 0 otherwise.

Usage,
    export NB_TOKEN="..."
    ./netbox_fill_participant_vlan_names.py              # dry run (default)
    ./netbox_fill_participant_vlan_names.py --apply      # commit renames
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from netbox_common import (
    HALL_VLAN_BASE,
    INFRASTRUCTURE_VIDS,
    NetboxClient,
    assert_junos_identifier,
    require_token,
)

# Upper bound on the table number within a hall, the lower bound is
# `vid > base`. 99 leaves room for a hundred tables per hall and
# keeps the windows non overlapping with the current bases
# (C: 101..199, D: 201..299). Lift this only after confirming that
# no other hall's base falls inside the extended window.
MAX_PARTICIPANT_TABLES_PER_HALL = 99

# The exact shape of an auto generated VLAN name the script will
# overwrite. The digit suffix must equal the VID, anything else is
# treated as operator intent and left alone.
AUTO_NAME_RE = re.compile(r"^VLAN(\d+)$")


def hall_and_table(vid: int) -> tuple[str, int] | None | str:
    """
    Resolve a VID to `(hall, table)` or to a sentinel,

      `None`         vid is in INFRASTRUCTURE_VIDS or no hall claims it.
      `"ambiguous"`  two or more halls' windows would claim this vid,
                     the operator must resolve the overlap before the
                     script can act.

    The arithmetic is the unbounded `base + table` rule from
    `participant_vlan_for_table`, the bound is only on the result's
    table number, not on the vid range.
    """
    if vid in INFRASTRUCTURE_VIDS:
        return None
    candidates: list[tuple[str, int]] = []
    for hall, base in HALL_VLAN_BASE.items():
        table = vid - base
        if 0 < table <= MAX_PARTICIPANT_TABLES_PER_HALL:
            candidates.append((hall, table))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) >= 2:
        return "ambiguous"
    return None


def target_name(vid: int) -> str | None:
    """Build the per table VLAN name for a vid, or None if non-classifiable."""
    ht = hall_and_table(vid)
    if isinstance(ht, tuple):
        hall, table = ht
        return f"{hall}_{table}"
    return None


def scope_key(vlan: dict) -> tuple[int | None, int | None]:
    """
    NetBox enforces VLAN name uniqueness within `(site, group)` scope.
    Two VLANs share a name-conflict scope iff this tuple is equal.
    """
    site_id = (vlan.get("site") or {}).get("id")
    group_id = (vlan.get("group") or {}).get("id")
    return (site_id, group_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually PATCH. Default is dry run.")
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()

    vlans = client.get_all("ipam/vlans/")
    print(f"Fetched {len(vlans)} VLANs from NetBox")

    # ----- Stage 1, classify every VLAN ------------------------------
    # status is the per VLAN tag, vlan keeps the object for the apply
    # phase, target is the proposed name (only set for plan candidates).
    plan: list[tuple[dict, str, str]] = []
    statuses: list[tuple[dict, str]] = []
    auto_named_unhandled: list[dict] = []

    # Pre index by (site_id, vid) so we can detect VID duplicates.
    by_site_vid: dict[tuple, list[dict]] = defaultdict(list)
    for v in vlans:
        if v.get("vid") is not None:
            by_site_vid[((v.get("site") or {}).get("id"), v["vid"])].append(v)
    duplicate_vid_ids = {
        v["id"]
        for vs in by_site_vid.values() if len(vs) > 1
        for v in vs
    }

    for vlan in vlans:
        vid = vlan.get("vid")
        if vid is None:
            statuses.append((vlan, "[skip-no-vid]"))
            continue
        if vlan["id"] in duplicate_vid_ids:
            statuses.append((vlan, "[CONFLICT-duplicate-vid]"))
            continue

        ht = hall_and_table(vid)
        if ht == "ambiguous":
            statuses.append((vlan, "[CONFLICT-ambiguous-hall]"))
            continue
        if ht is None:
            current = vlan.get("name") or ""
            m = AUTO_NAME_RE.match(current)
            # An auto generated name we cannot classify deserves a
            # loud warning, the operator either renames it manually
            # or extends HALL_VLAN_BASE / INFRASTRUCTURE_VIDS.
            if m and int(m.group(1)) == vid:
                statuses.append((vlan, "[warn-unhandled-autoname]"))
                auto_named_unhandled.append(vlan)
            else:
                statuses.append((vlan, "[skip-non-participant]"))
            continue

        # Classified as a participant. Only rename auto generated
        # current names, leave anything else alone.
        target = target_name(vid)
        current = vlan.get("name") or ""
        if current == target:
            statuses.append((vlan, "[ok]"))
            continue
        m = AUTO_NAME_RE.match(current)
        if not (m and int(m.group(1)) == vid):
            statuses.append((vlan, f"[skip-custom-name] {current!r}"))
            continue

        # Defense in depth, the renderer's identifier check would
        # catch this later but we'd rather fail before any PATCH.
        try:
            assert_junos_identifier(target, f"target name for VID {vid}")
        except RuntimeError as exc:
            statuses.append((vlan, f"[FAIL-bad-target] {exc}"))
            continue

        plan.append((vlan, current, target))

    # ----- Stage 2, target name collision check ----------------------
    # Build the post-rename name index per (site, group) scope. Any
    # proposed rename whose target name is already taken by some other
    # VLAN in the same scope, or by two proposed renames at once, is
    # refused before any PATCH leaves the script.
    existing_names: dict[tuple, set[str]] = defaultdict(set)
    for v in vlans:
        existing_names[scope_key(v)].add(v.get("name") or "")

    # Map from proposed (scope, target) -> list of vlans planning that name.
    proposed_per_scope: dict[tuple, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list))
    for vlan, _current, target in plan:
        proposed_per_scope[scope_key(vlan)][target].append(vlan)

    conflict_ids: set[int] = set()
    for scope, targets in proposed_per_scope.items():
        for target, vlans_planning in targets.items():
            if len(vlans_planning) > 1:
                for v in vlans_planning:
                    conflict_ids.add(v["id"])
            # Collision with an existing VLAN that the script is not
            # itself going to rename out of the way.
            if target in existing_names[scope]:
                # Find the VLAN that already holds the name and check
                # if it is itself in the plan.
                planned_renamers = {v["id"] for v in vlans_planning}
                holders = [
                    v for v in vlans
                    if scope_key(v) == scope
                    and (v.get("name") or "") == target
                    and v["id"] not in planned_renamers
                ]
                if holders:
                    for v in vlans_planning:
                        conflict_ids.add(v["id"])

    # Promote collision-tagged plan entries to status, drop them from plan.
    final_plan: list[tuple[dict, str, str]] = []
    for vlan, current, target in plan:
        if vlan["id"] in conflict_ids:
            statuses.append((vlan, f"[CONFLICT-name-taken] target {target!r}"))
        else:
            final_plan.append((vlan, current, target))

    # ----- Stage 3, dry run print or apply ---------------------------
    # Sort all statuses by vid for the printed output.
    def vid_key(item):
        return item[0].get("vid") or 0

    statuses.sort(key=vid_key)
    final_plan.sort(key=lambda x: vid_key((x[0],)))

    plan_ids = {v["id"] for v, _c, _t in final_plan}

    # Print every per VLAN status the operator might care about,
    # suppress only the bulk of `[skip-non-participant]` lines.
    counts: dict[str, int] = defaultdict(int)
    for vlan, status in statuses:
        if vlan["id"] in plan_ids:
            continue  # plan entries are printed in the next loop
        if status == "[skip-non-participant]":
            counts["[skip-non-participant]"] += 1
            continue
        counts[status.split(" ", 1)[0]] += 1
        vid = vlan.get("vid")
        name = vlan.get("name") or "<unnamed>"
        print(f"  vid {vid!s:>5}  {status}  {name!r}")

    for vlan, current, target in final_plan:
        if args.apply:
            client.patch(f"ipam/vlans/{vlan['id']}/", {"name": target})
            tag = "[renamed]"
        else:
            tag = "[dry_rename]"
        counts[tag] += 1
        vid = vlan.get("vid")
        print(f"  vid {vid!s:>5}  {tag} {current!r} -> {target!r}")

    print()
    print("=" * 60)
    for bucket in sorted(counts):
        print(f"  {bucket:<28} {counts[bucket]}")
    print("=" * 60)

    failed_buckets = {
        b for b in counts
        if b.startswith("[CONFLICT") or b.startswith("[FAIL") or b == "[warn-unhandled-autoname]"
    }
    if failed_buckets:
        print("\nReview the lines above tagged "
              + ", ".join(sorted(failed_buckets)) + ".")

    if not args.apply:
        print("\nDry run complete. Re run with --apply to commit the renames.")

    return 2 if failed_buckets else 0


if __name__ == "__main__":
    sys.exit(main())
