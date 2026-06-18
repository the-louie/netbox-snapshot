#!/usr/bin/env python3
"""
Render per access switch static Cisco IOS config from NetBox.

Loads a Jinja2 template (default `scripts/templates/access.conf.j2`) and
renders it once per device with role `access_switch`, writing each output
as `<name>.conf` lowercased (see `access_config_filename`). The file is the
config the switch fetches over TFTP after its VLAN 1 DHCP bootstrap.

Background. Access switches do not persist config to flash. They boot, DHCP
on VLAN 1, and are handed a TFTP `next-server` plus a per switch
`boot-file-name` by Kea (matched on the Option 82 circuit id the dist
injects). The file this script renders sets the switch's hostname, its
static VLAN 600 management IP and mask, and its default gateway, brings up
VLAN 600, and drops the VLAN 1 address. There is no second DHCP exchange on
VLAN 600, management addressing is fully static and sourced from NetBox.

The filename this script writes MUST equal the `boot-file-name`
`netbox2kea.py` emits for the same switch, both call
`access_config_filename` so the two artefacts agree byte for byte.

Every dynamic value is sourced from NetBox, no fallback constants,
  hostname     = Device.name                       (for example D39A)
  ip-address   = Vlan600 SVI IP, falling back to primary_ip4 (with prefix)
  mask         = dotted netmask of that address's /24
  gateway      = first usable host of that /24 (the per district irb.600)

The gateway is derived from the switch's own management /24 so the script
is self contained. It is cross checked against the uplink dist's irb.600
address, a divergence is warned about but does not block rendering.

Requires `jinja2`, the only third party dependency for the renderer.

Usage,
    export NB_TOKEN="..."
    ./netbox2cisco.py
    ./netbox2cisco.py --outdir ./out
    ./netbox2cisco.py --template ./alternative.access.j2
    ./netbox2cisco.py --access D39A
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys

from netbox_utils.netbox_common import (
    NETBOX_HOST,
    NetboxClient,
    ROLE_ACCESS,
    ROLE_DIST,
    access_config_filename,
    access_uplinks,
    assert_cisco_hostname,
    atomic_write_text,
    configure_logging,
    confirm_overwrite,
    dist_info_from_device,
    require_token,
)

# Module logger, named after the script so a multi script run can be
# filtered by source. The actual handler and level are wired up by
# `configure_logging` in main, this just gives the module a stable name.
log = logging.getLogger("netbox2cisco")


def build_access_context(dev: dict,
                         vlan600_by_id: dict[int, str],
                         uplinks: dict[int, list[tuple[dict, dict]]],
                         dists_info_by_name: dict[str, dict]) -> dict:
    """
    Build the Jinja context dict for one access switch. Raises
    RuntimeError when a required value is missing or malformed, which the
    caller records as a per switch failure rather than aborting the run.

    The management address is resolved from the Vlan600 SVI, falling back
    to primary_ip4. Unlike the Kea reservation path the prefix length is
    load bearing here, the mask and the gateway both derive from it, so a
    bare address with no `/len` is rejected.
    """
    name = dev["name"]
    # Reject names that are not legal Cisco IOS hostnames before they
    # flow into the `hostname` line of the rendered config or into the
    # access_config_filename path. NetBox does not constrain Device
    # names, so a name like `D39A; end\n!\nenable secret 0 attacker`
    # would otherwise inject IOS commands into every replay, and a
    # name like `D39A/../etc/passwd` would otherwise escape `--outdir`
    # when the helper builds the boot file path.
    assert_cisco_hostname(name, f"access switch hostname (Device {name!r})")

    addr = vlan600_by_id.get(dev["id"])
    source = "Vlan600 SVI"
    if not addr:
        addr = (dev.get("primary_ip4") or {}).get("address")
        source = "primary_ip4"
    if not addr:
        raise RuntimeError(
            f"{name} has no Vlan600 SVI IP and no primary_ip4 in NetBox, "
            f"cannot render a static management config"
        )
    if "/" not in addr:
        raise RuntimeError(
            f"{name} management address {addr!r} ({source}) has no prefix "
            f"length, the static config needs a subnet mask"
        )
    iface = ipaddress.ip_interface(addr)
    if iface.version != 4:
        raise RuntimeError(
            f"{name} management address {addr!r} is not IPv4"
        )
    network = iface.network
    if network.prefixlen != 24:
        raise RuntimeError(
            f"{name} management address {addr!r} is /{network.prefixlen}, "
            f"the access config convention is a /24 management subnet"
        )
    ip_address = str(iface.ip)
    mask = str(network.netmask)
    # The gateway is the dist's actual irb.600 address read from NetBox
    # when the access switch's uplink cable resolves to a known dist.
    # The convention per the howto is .1 of the mgmt /24, the script
    # falls back to that when no uplink dist info is resolvable. Using
    # the dist's real address protects against any future district that
    # parks irb.600 on a non .1 host bit (HSRP-like, second hop on the
    # same subnet), the previous derivation would have shipped switches
    # with a blackhole gateway in that case.
    fallback_gateway = str(network.network_address + 1)
    gateway = fallback_gateway

    edges = uplinks.get(dev["id"]) or []
    if not edges:
        log.warning("%s has no uplink cable to a dist in NetBox, falling "
                    "back to the .1 convention for gateway", name)
    else:
        if len(edges) > 1:
            log.warning("%s has %d uplink cables, using the first for "
                        "gateway resolution", name, len(edges))
        _dist_port, dist_dev = edges[0]
        dist_info = dists_info_by_name.get(dist_dev.get("name"))
        if dist_info and dist_info.get("mgmt_gateway"):
            gateway = dist_info["mgmt_gateway"]
            if gateway != fallback_gateway:
                log.warning(
                    "%s uplink dist %s has irb.600 at %s, using that "
                    "instead of the .1 convention %s",
                    name, dist_dev.get("name"), gateway, fallback_gateway,
                )
        else:
            log.warning(
                "%s uplink dist %s has no resolvable mgmt_gateway in "
                "NetBox, falling back to the .1 convention",
                name, dist_dev.get("name"),
            )

    return {
        "netbox_host": NETBOX_HOST,
        "hostname":    name,
        "ip_address":  ip_address,
        "mask":        mask,
        "gateway":     gateway,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outdir",
        default=".",
        help="Directory to write per switch files into. Default cwd.",
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
        help="Path to the Jinja template, default "
             "<scriptdir>/templates/access.conf.j2.",
    )
    parser.add_argument(
        "--access",
        metavar="NAME",
        help="Only render this access switch.",
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
        from jinja2 import (Environment, FileSystemLoader, StrictUndefined,
                            UndefinedError)
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
        tpl_name = "access.conf.j2"

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

    access_devices = client.get_all(f"dcim/devices/?role={ROLE_ACCESS}")
    if args.access:
        access_devices = [d for d in access_devices if d["name"] == args.access]
        if not access_devices:
            print(f"Error, access switch {args.access!r} not found.",
                  file=sys.stderr)
            return 1
    print(f"Found {len(access_devices)} access switch device(s)")

    # NetBox enforces device name uniqueness per Site (or globally,
    # depending on settings). Two access switches sharing a name would
    # render to the same lowercased boot file and overwrite each other
    # silently, the second write wins and the first switch boots into
    # the wrong config. Detect the duplicate up front and refuse, the
    # operator fixes the name in NetBox before any file is written.
    name_to_ids: dict[str, list[int]] = {}
    for d in access_devices:
        name_to_ids.setdefault(d["name"], []).append(d["id"])
    duplicates = {n: ids for n, ids in name_to_ids.items() if len(ids) > 1}
    if duplicates:
        for n, ids in sorted(duplicates.items()):
            print(f"Error, access switch name {n!r} is shared by Device "
                  f"ids {ids}, both would render to the same file. "
                  f"Rename one in NetBox.", file=sys.stderr)
        return 1

    access_by_id = {d["id"]: d for d in access_devices}

    # Vlan600 SVI IP per device, kept with its prefix length because the
    # mask and gateway both derive from it. Narrowing the query to the
    # access devices avoids pulling the entire IPAM tree.
    #
    # NetBox accepts two IPAddress objects assigned to the same Vlan600
    # SVI on a single Device. The previous one-line dict assignment
    # silently kept the last one seen, which is non deterministic
    # (NetBox pagination order is not guaranteed) and would hide an
    # operator data error behind a randomly chosen address. Collect
    # every match per device first, then assert exactly one.
    vlan600_by_id: dict[int, str] = {}
    if access_by_id:
        ids_param = ",".join(str(i) for i in access_by_id)
        access_ips = client.get_all(
            f"ipam/ip-addresses/?device_id__in={ids_param}"
        )
        candidates_by_id: dict[int, list[str]] = {}
        for ip in access_ips:
            ao = ip.get("assigned_object") or {}
            if ao.get("name") != "Vlan600":
                continue
            dev = ao.get("device") or {}
            dev_id = dev.get("id")
            if dev_id in access_by_id:
                candidates_by_id.setdefault(dev_id, []).append(ip["address"])
        multi: list[tuple[str, list[str]]] = []
        for dev_id, addrs in candidates_by_id.items():
            if len(addrs) > 1:
                multi.append((access_by_id[dev_id]["name"], addrs))
            else:
                vlan600_by_id[dev_id] = addrs[0]
        if multi:
            for name, addrs in sorted(multi):
                print(f"Error, access switch {name!r} has {len(addrs)} "
                      f"IPs assigned to Vlan600 in NetBox, expected "
                      f"exactly one, {addrs}. Fix the SVI in NetBox.",
                      file=sys.stderr)
            return 1

    # Uplink edges, the single mapping between the access and dist layers,
    # resolved once through the shared helper so this script, netbox2kea,
    # and the verify script all read the same cables.
    cables = client.get_all("dcim/cables/")
    uplinks = access_uplinks(cables, access_by_id)

    # Per dist info for the gateway cross check, best effort. A dist whose
    # info cannot be resolved (missing irb.600, no Location) simply means
    # the cross check is skipped for its access switches, the gateway
    # still derives from each switch's own /24.
    dists_info_by_name: dict[str, dict] = {}
    for device in client.get_all(f"dcim/devices/?role={ROLE_DIST}"):
        try:
            dists_info_by_name[device["name"]] = dist_info_from_device(
                client, device)
        except RuntimeError as exc:
            log.debug("dist %s info unavailable for gateway cross check, %s",
                      device.get("name"), exc)

    # Build the full list of paths the renderer is about to write so the
    # overwrite guard can refuse silently clobbering operator output.
    intended_paths = [os.path.join(args.outdir, access_config_filename(d))
                      for d in access_devices]
    if not confirm_overwrite(intended_paths, args.overwrite):
        return 1

    written = 0
    failures = 0
    # Catching specific exception families keeps unexpected programming
    # errors visible while still recording per switch runtime failures.
    handled_failures = (RuntimeError, KeyError, UndefinedError, ValueError)
    for dev in sorted(access_devices, key=lambda d: d["name"]):
        try:
            ctx = build_access_context(dev, vlan600_by_id, uplinks,
                                       dists_info_by_name)
            rendered = template.render(**ctx)
            path = os.path.join(args.outdir, access_config_filename(dev))
            atomic_write_text(path, rendered)
            print(f"  wrote {path}  ({ctx['ip_address']} {ctx['mask']}, "
                  f"gw {ctx['gateway']})")
            written += 1
        except handled_failures as exc:
            print(f"  [FAIL] {dev['name']}, {exc}", file=sys.stderr)
            failures += 1

    print()
    print("=" * 60)
    print(f"  Rendered {written}/{len(access_devices)} access configs into "
          f"{args.outdir}")
    if failures:
        # Mirror the failure count to stderr so a CI run that tails
        # only stdout still sees an actionable signal alongside the
        # exit 2. The per switch [FAIL] lines were already on stderr,
        # the summary aggregate now lands there too.
        print(f"  Failures, {failures}")
        print(f"  Failures, {failures} access switch(es) did not "
              f"render, see [FAIL] lines above", file=sys.stderr)
    print("=" * 60)

    # The operator guidance block exists for interactive runs, in a CI
    # pipeline the block adds noise to the run log without changing
    # the behaviour. Skipping on a non TTY stdout is the conservative
    # default, the summary above stays in every run.
    if sys.stdout.isatty():
        print()
        print("Next,")
        print("  1. Copy these files to the TFTP root (tftp.infra.glitched.se)")
        print("  2. Re-run netbox2kea.py so each switch's boot-file-name "
              "reservation matches")
        print("  3. Reboot or bounce the access switch to refetch its config")

    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
