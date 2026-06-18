#!/usr/bin/env python3
"""
Render per-dist Junos `set`-syntax config from NetBox into the current directory.

Loads a Jinja2 template (default `scripts/templates/dist.set.j2`) and renders
it once per device with role `distribution_switches`, writing each output as
`<dist-name>.set`.

The template carries the Junos structure. The script does three things,
pulling NetBox state, building a per dist context dict, then rendering the
template. The static platform template (the groups that hold the firewall
filters, the OSPF policies, the SNMP credentials, and the uplink trunks)
remains hand maintained and gets concatenated separately before deploy.

Requires `jinja2`, the only third party dependency for the renderer.

Usage,
    export NB_TOKEN="..."
    ./netbox2junos.py
    ./netbox2junos.py --outdir ./out
    ./netbox2junos.py --template ./alternative.set.j2
    ./netbox2junos.py --dist D-THE-FORGE-SW


/// Louie - 2026
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import re
import sys
from collections import defaultdict

from netbox_utils.netbox_common import (
    NETBOX_HOST,
    NetboxClient,
    ROLE_DIST,
    configure_logging,
    confirm_overwrite,
    kea_service_ips,
    require_token,
    vlan_name_for,
    vlans_by_vid,
)

# Module logger, named after the script so a multi script run can be
# filtered by source. The actual handler and level are wired up by
# `configure_logging` in main, this just gives the module a stable name.
log = logging.getLogger("netbox2junos")

# VLAN id of the mgmt SVI. The Junos VLAN name and the IRB description
# for this VLAN are sourced from the NetBox VLAN.name field, scoped to
# the dist's site so each district can carry its own name.
MGMT_VID = 600

# OSPF linknet VLAN ids that the renderer expects to find in NetBox.
# Each VLAN object supplies the description string used on the matching
# OSPF-DEFAULT and OSPF-INTERNET IRB unit.
OSPF_DEFAULT_VIDS = (1100, 1200)
OSPF_INTERNET_VIDS = (1101, 1201)

# Other infrastructure VLAN ids that the participant IRB loop must
# ignore. The two values cover platform internal IRBs that some dists
# carry but the renderer does not need to emit.
OTHER_INFRA_VIDS = (500, 501)

# Regex shapes used to identify the dist's logical interfaces and the cabled
# downlink ports. Bound at module load to avoid re compilation per call.
IRB_RE = re.compile(r"^irb\.(\d+)$")
GE_RE = re.compile(r"^ge-0/0/(\d+)$")

# VLAN ids that the renderer handles through dedicated paths or skips
# entirely. The participant IRB loop excludes these so an accidental
# irb.600 entry on a dist does not get double rendered, and so platform
# internal IRBs do not surface as participant entries. Everything
# outside this set is treated as participant or crew traffic, which
# lets non table areas use any operator chosen VLAN id without a code
# change.
#
# Deliberate divergence from the shared `INFRASTRUCTURE_VIDS` constant
# in `netbox_utils/netbox_common.py`, which includes the crew VID 199.
# The Junos renderer DOES emit a participant style IRB for irb.199 (the
# INFRA crew VLAN, see the comment on `participants` below), so 199
# stays outside this set. The Kea renderer, the verify script, and the
# auto naming script all include 199 in their exclusion (the crew Kea
# subnet is emitted through the kea-crew Prefix role, not as a
# participant), so they pull the shared constant. If this renderer ever
# stops emitting an IRB for VID 199, collapse INFRA_IRB_VIDS into
# INFRASTRUCTURE_VIDS at that point.
INFRA_IRB_VIDS = frozenset({
    MGMT_VID,
    *OSPF_DEFAULT_VIDS,
    *OSPF_INTERNET_VIDS,
    *OTHER_INFRA_VIDS,
})


def build_context(dist: dict, client: NetboxClient,
                  dhcp4_servers: list[str],
                  dhcp6_servers: list[str],
                  ipv6_ra_dns: list[str],
                  vlan_index: dict[tuple[int | None, int], dict]) -> dict:
    """
    Build the Jinja context dict for one dist. The shape of this dict is
    documented at the top of `templates/dist.set.j2`. Any variable referenced
    by the template that is not provided here will raise an UndefinedError,
    which surfaces template and code drift loudly.

    Fleet wide service IPs and the VLAN index are passed in by the
    caller, they are resolved from NetBox once per run rather than per
    dist. The VLAN index supplies every Junos VLAN name and every
    IRB description used in the rendered config, no name is generated.
    """
    dev_id = dist["id"]
    name = dist["name"]
    site_id = (dist.get("site") or {}).get("id")

    ifaces = client.get_all(f"dcim/interfaces/?device_id={dev_id}")
    ips = client.get_all(f"ipam/ip-addresses/?device_id={dev_id}")

    by_name = {i["name"]: i for i in ifaces}
    v4_by_iface: dict[int, list[str]] = defaultdict(list)
    v6_by_iface: dict[int, list[str]] = defaultdict(list)
    for ip in ips:
        assigned = ip.get("assigned_object") or {}
        iface_id = assigned.get("id")
        if iface_id is None:
            continue
        if ipaddress.ip_interface(ip["address"]).version == 4:
            v4_by_iface[iface_id].append(ip["address"])
        else:
            v6_by_iface[iface_id].append(ip["address"])

    def find_iface(iface_name: str) -> dict | None:
        """
        Resolve an interface dict from `by_name` with a case-insensitive
        fallback. Junos itself is case sensitive on interface names, so the
        rendered config always uses the canonical lowercase form. NetBox
        accepts any case on the operator-typed name, the fallback exists
        to surface the typo loudly rather than silently dropping the
        interface and the IPs assigned to it.
        """
        iface = by_name.get(iface_name)
        if iface is not None:
            return iface
        lower = iface_name.lower()
        for actual_name, actual_iface in by_name.items():
            if actual_name.lower() == lower:
                log.warning(
                    "%s interface %r found in NetBox as %r, case mismatch. "
                    "Rename the interface in NetBox to the lowercase form, "
                    "Junos requires it.", name, iface_name, actual_name,
                )
                return actual_iface
        return None

    def first_ip(iface_name: str, family: str) -> str | None:
        iface = find_iface(iface_name)
        if iface is None:
            return None
        bucket = v4_by_iface if family == "inet" else v6_by_iface
        addrs = bucket.get(iface["id"]) or []
        return addrs[0] if addrs else None

    # OSPF linknet IRBs, the renderer needs both v4 and v6 on each.
    # The IRB description is the NetBox VLAN name for the matching VID,
    # which the operator sets once per OSPF VLAN object.
    ospf: dict[int, dict] = {}
    for vid in (*OSPF_DEFAULT_VIDS, *OSPF_INTERNET_VIDS):
        ospf[vid] = {
            "description": vlan_name_for(vlan_index, vid, site_id,
                                         f"OSPF irb.{vid} on {name}"),
            "v4": first_ip(f"irb.{vid}", "inet"),
            "v6": first_ip(f"irb.{vid}", "inet6"),
        }

    # Mgmt SVI VLAN name, sourced from the NetBox VLAN with vid 600
    # scoped to the dist's site. The same string is used as the Junos
    # VLAN name and as the irb.600 description.
    mgmt_vlan_name = vlan_name_for(vlan_index, MGMT_VID, site_id,
                                   f"mgmt irb.{MGMT_VID} on {name}")

    # Participant and crew IRBs, identified by any irb.<vid> on the dist
    # that has an IPv4 assigned and a VLAN id outside the infrastructure
    # set. The VLAN id is accepted verbatim from the interface name, the
    # corresponding NetBox VLAN object supplies the human readable name
    # used in both the Junos VLANS group and the IRB description.
    #
    # An IRB with no IPv4 is treated as parked and skipped so an operator
    # can stage an interface in NetBox before the deployment is ready.
    # An IRB with v4 but no v6 (or vice versa) is rendered with whatever
    # families NetBox carries, the template gates the v6 lines on the
    # presence of the v6 address. Crew networks that only need v4 (e.g.
    # the INFRA crew VLAN 199) are the canonical case for v4-only.
    participants = []
    for iname in by_name:
        match = IRB_RE.match(iname)
        if not match:
            continue
        vid = int(match.group(1))
        if vid in INFRA_IRB_VIDS:
            continue
        ipv4 = first_ip(iname, "inet")
        if not ipv4:
            continue
        ipv6 = first_ip(iname, "inet6")
        vlan_name = vlan_name_for(vlan_index, vid, site_id,
                                  f"participant {iname} on {name}")
        # The Junos VLAN name carries an underscore between the hall letter
        # and the table number (e.g. D_39) because Junos VLAN names cannot
        # start with a digit. The canonical 2025 dist configs (see
        # Config-Dist/D-THE-FORGE-SW-PASTED-T-SW.txt) drop the underscore
        # in the human-readable IRB description (D39 not D_39), so the
        # renderer matches that convention. Names without underscores
        # (e.g. INFRA-CREW) pass through unchanged.
        participants.append({
            "vid": vid,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "description": vlan_name.replace("_", ""),
            "vlan_name": vlan_name,
        })
    participants.sort(key=lambda p: p["vid"])

    # Cabled access ports, the renderer reads the VLAN id from the dist
    # port's untagged_vlan binding in NetBox. A cabled ge-0/0/N port must
    # carry both a description and an untagged_vlan, the script fails the
    # dist rather than silently skipping incomplete data. The description
    # stays free form, the Junos relay agent copies it verbatim into
    # Option 82, so non table areas can use any naming convention the
    # operator picks.
    cabled_ports = []
    for iname, iface in by_name.items():
        ge_match = GE_RE.match(iname)
        if not ge_match:
            continue
        if not iface.get("cable"):
            continue
        desc = (iface.get("description") or "").strip()
        if not desc:
            raise RuntimeError(
                f"Port {iname} on {name} is cabled but has no "
                f"description in NetBox"
            )
        untagged = iface.get("untagged_vlan") or {}
        vid = untagged.get("vid")
        if vid is None:
            raise RuntimeError(
                f"Port {iname} on {name} is cabled but has no "
                f"untagged_vlan binding in NetBox, set the access VLAN"
            )
        cabled_ports.append({
            "port_num": int(ge_match.group(1)),
            "description": desc,
            "vid": vid,
        })
    cabled_ports.sort(key=lambda p: p["port_num"])

    loopback_v4 = first_ip("lo0.0", "inet")
    if loopback_v4 is None:
        raise RuntimeError(
            f"Device {name!r} has no IPv4 assigned to interface lo0.0 "
            f"in NetBox. Every dist needs a loopback /32, see "
            f"reference_documentation/howto/add-a-dist.md section 8."
        )

    return {
        "netbox_host": NETBOX_HOST,
        "dist_name": name,
        "mgmt_vlan_name": mgmt_vlan_name,
        "loopback_v4": loopback_v4,
        "mgmt_v4": first_ip("irb.600", "inet"),
        "ospf": ospf,
        "participants": participants,
        "cabled_ports": cabled_ports,
        "dhcp4_servers": dhcp4_servers,
        "dhcp6_servers": dhcp6_servers,
        "ipv6_ra_dns": ipv6_ra_dns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outdir",
        default=".",
        help="Directory to write per dist files into. Default cwd.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing files in --outdir without prompting. The "
             "default behaviour prompts on a TTY and refuses on a non "
             "interactive run.",
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Path to the Jinja template, default <scriptdir>/templates/dist.set.j2.",
    )
    parser.add_argument(
        "--dist",
        metavar="NAME",
        help="Only render this dist.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Set logging to DEBUG. Default is INFO, which matches the "
             "existing progress output.",
    )
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)

    if not require_token():
        return 1

    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError
    except ImportError:
        # On the operator host pip is not installed by default, the apt
        # package is the recommended install route. The pip route stays
        # as a secondary suggestion for environments that have it.
        print("Error, jinja2 is required. Install on Debian or Ubuntu with, "
              "sudo apt install -y python3-jinja2. Other environments, "
              "pip install jinja2.", file=sys.stderr)
        return 1

    # Template path resolution prefers an explicit CLI argument, falls back
    # to the templates directory shipped alongside the script.
    if args.template:
        tpl_dir = os.path.dirname(os.path.abspath(args.template)) or "."
        tpl_name = os.path.basename(args.template)
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        tpl_dir = os.path.join(script_dir, "templates")
        tpl_name = "dist.set.j2"

    tpl_path = os.path.join(tpl_dir, tpl_name)
    if not os.path.exists(tpl_path):
        print(f"Error, template not found at {tpl_path}", file=sys.stderr)
        return 1
    print(f"Template, {tpl_path}")

    env = Environment(
        loader=FileSystemLoader(tpl_dir),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = env.get_template(tpl_name)

    os.makedirs(args.outdir, exist_ok=True)

    client = NetboxClient()

    # Resolve fleet wide service IPs once before the per dist loop.
    # NetBox is the source of truth. DHCPv4 is required, an empty result
    # fails the run with exit 2 (incomplete NetBox state). The v6 lookups
    # warn and continue, the template gates the dhcpv6 relay block and
    # the per IRB RA dns lines on the presence of these lists so a v4
    # only fleet renders cleanly.
    dhcp4_servers = kea_service_ips(client, "dhcp0", family=4)
    dhcp6_servers = kea_service_ips(client, "dhcp0", family=6)
    ipv6_ra_dns = kea_service_ips(client, "ns0", family=6)
    if not dhcp4_servers:
        print("Error, no IPAddress with dns_name starting dhcp0 (family 4)",
              file=sys.stderr)
        return 2
    if not dhcp6_servers:
        log.warning("no IPAddress with dns_name starting dhcp0 (family 6), "
                    "dhcpv6 relay block will be omitted from every dist")
    if not ipv6_ra_dns:
        log.warning("no IPAddress with dns_name starting ns0 (family 6), "
                    "per IRB RA dns-server-address lines will be omitted")
    print(f"DHCPv4 relay servers, {dhcp4_servers}")
    print(f"DHCPv6 relay servers, {dhcp6_servers}")
    print(f"IPv6 RA DNS,          {ipv6_ra_dns}")

    vlan_index = vlans_by_vid(client)
    print(f"VLANs fetched,        {len(vlan_index)}")

    dists = client.get_all(f"dcim/devices/?role={ROLE_DIST}")
    if args.dist:
        dists = [d for d in dists if d["name"] == args.dist]
        if not dists:
            print(f"Error, dist {args.dist!r} not found.", file=sys.stderr)
            return 1
    print(f"Found {len(dists)} dist device(s)")

    # Build the full list of paths the renderer is about to write so the
    # overwrite guard can refuse silently clobbering operator output. A
    # non TTY run without --overwrite is refused, a TTY run prompts.
    intended_paths = [os.path.join(args.outdir, f"{d['name']}.set")
                      for d in dists]
    if not confirm_overwrite(intended_paths, args.overwrite):
        return 1

    written = 0
    failures = 0
    # Catching specific exception families keeps unexpected programming
    # errors visible while still recording per dist runtime failures.
    handled_failures = (RuntimeError, KeyError, UndefinedError, ValueError)
    for dist in sorted(dists, key=lambda d: d["name"]):
        try:
            ctx = build_context(dist, client,
                                dhcp4_servers, dhcp6_servers, ipv6_ra_dns,
                                vlan_index)
            rendered = template.render(**ctx)
            path = os.path.join(args.outdir, f"{dist['name']}.set")
            with open(path, "w") as fh:
                fh.write(rendered)
            line_count = rendered.count("\n")
            print(f"  wrote {path}  ({line_count} lines, "
                  f"{len(ctx['participants'])} IRBs, "
                  f"{len(ctx['cabled_ports'])} cabled ports)")
            written += 1
        except handled_failures as exc:
            print(f"  [FAIL] {dist['name']}, {exc}", file=sys.stderr)
            failures += 1

    print()
    print("=" * 60)
    print(f"  Rendered {written}/{len(dists)} dist files into {args.outdir}")
    if failures:
        print(f"  Failures, {failures}")
    print("=" * 60)

    # The operator guidance block exists for interactive runs, in a CI
    # pipeline the block adds noise to the run log without changing
    # the behaviour. Skipping on a non TTY stdout is the conservative
    # default, the summary above stays in every run.
    if sys.stdout.isatty():
        print()
        print("Next,")
        print("  1. cat <static-platform-template>.set <dist>.set > <dist>.full.set")
        print("  2. On a test EX4300-24T,")
        print("       load merge terminal relative")
        print("       show | compare")
        print("       commit check")

    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
