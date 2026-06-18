#!/usr/bin/env python3
"""
Verify that NetBox state satisfies every requirement of the three active
renderers, `scripts/netbox2junos.py`, `scripts/netbox2kea.py`, and
`scripts/netbox2cisco.py`.

Output is a running checklist, one line per check, ending in OK / WARN /
FAIL. Designed for operators to run after any NetBox UI session so a
missing or inconsistent value surfaces before a renderer run hits it.

The script reads NetBox only, no mutations. It groups checks into,

  Fleet bootstrap
    * IPAM roles (`kea-bootstrap`, `kea-crew`)
    * Fleet service IPAddresses (`dhcp0`, `ns0`, `ntp`, `tftp`)
    * Bootstrap and crew Prefix + IP Range pairs
    * Global OSPF VLANs (1100, 1101, 1200, 1201)

  Per dist (device with role `distribution_switches`)
    * Site, Location, Location.slug (Kea filename and slug uniqueness)
    * `lo0.0` exists + IPv4 + /32 prefix (Junos hard fail if missing)
    * Primary IPv4 matches the loopback IPv4 (operator convention)
    * `irb.600` exists + IPv4 + /24 + host bit .1 (Kea mgmt subnet)
    * Mgmt subnet_id (third octet * 10) uniqueness across dists
    * Mgmt /24 Prefix in IPAM + bound to VLAN 600 (downstream pitfall)
    * `irb.600` untagged_vlan binding = 600
    * OSPF IRBs (irb.1100/1101/1200/1201) exist + untagged_vlan
      binding = vid + v4 + v6
    * Mgmt VLAN 600 site scoped (warn if only global)
    * Participant IRBs, each with v4 has a named VLAN object whose
      name is a legal Junos identifier, and irb.<vid> untagged_vlan
      binding = vid (catches the wrong-VLAN-on-IRB pitfall)
    * Cabled `ge-0/0/N` ports each have description + mode=access +
      untagged_vlan + ASCII-only description

  Cables (access uplinks)
    * Every access switch has exactly one cable to the fleet
    * The cable's far end is on a dist in the fleet, on a `ge-0/0/N`

  Per access switch (device with role `access_switch`)
    * Vlan600 SVI interface present
    * Vlan600 IPv4 OR primary_ip4 set (netbox2cisco static config source)
    * That address is a /24 (the mask and the .1 gateway derive from it)
    * The uplink dist's mgmt /24 matches the switch's own /24 (warn on
      a per district mismatch, the gateway is the .1 of the switch's /24)
    * Device.name is a legal Cisco IOS hostname and yields a TFTP safe
      boot file name (netbox2cisco writes it, netbox2kea serves it)
    * primary_ip4 set (operator convention)
    * When `--dist NAME` is set, access switches whose uplink cable
      lands on the named dist are checked, others are excluded.
      Without `--dist` every access switch is checked.

Exit code is 2 when any FAIL appears, 0 otherwise. WARN does not
change the exit code, the renderer will still run.

Usage,
    export NB_TOKEN="..."
    ./netbox_verify_renderable.py
    ./netbox_verify_renderable.py --dist D-THE-FORGE-SW
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from collections import defaultdict

from netbox_utils.netbox_common import (
    INFRASTRUCTURE_VIDS,
    NetboxClient,
    ROLE_ACCESS,
    ROLE_DIST,
    access_config_filename,
    access_uplinks,
    dist_info_from_device,
    require_token,
    vlans_by_vid,
)

# Cisco IOS hostname shape, a letter followed by letters, digits, or
# hyphens. netbox2cisco.py writes Device.name into the `hostname` line and
# derives the TFTP boot file from it, an illegal name produces a config the
# switch rejects or a filename the TFTP server cannot serve.
IOS_HOSTNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")

REQUIRED_ROLES = ("kea-bootstrap", "kea-crew")
OSPF_VIDS = (1100, 1101, 1200, 1201)
MGMT_VID = 600
TFTP_DNS = "tftp.infra.glitched.se"
JUNOS_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
IRB_RE = re.compile(r"^irb\.(\d+)$")
GE_RE = re.compile(r"^ge-0/0/\d+$")

# (dns_name prefix, family, severity, purpose)
FLEET_IPS = (
    ("dhcp0", 4, "FAIL", "Junos and Kea DHCPv4 relay"),
    ("dhcp0", 6, "WARN", "Junos DHCPv6 relay, renderer warn only"),
    ("ns0",   4, "FAIL", "Kea Option 6 DNS servers"),
    ("ns0",   6, "WARN", "Junos IPv6 RA DNS, renderer warn only"),
    ("ntp",   4, "FAIL", "Kea Option 42 NTP servers"),
    ("tftp",  4, "FAIL", "Kea TFTP next-server"),
)

# Mitigation metadata keyed by check kind. Each WARN/FAIL bucket in the
# end-of-run summary shows the Purpose plus the See reference, sourced
# from this table. Keep the howto reference and the verify check in
# lockstep, every check kind needs an entry, every howto section that
# a check touches needs at least one kind pointing at it.
HOWTO = "reference_documentation/howto/add-a-dist.md"

CHECK_META: dict[str, tuple[str, str]] = {
    # (kind) -> (purpose, see)
    "ipam-role": (
        "IPAM Roles are how the renderers find the right Prefix or IP "
        "Range. Without the role, queries that filter on it return empty "
        "and the renderer cannot locate its subnets or pools.",
        f"{HOWTO} §0 (Fleet bootstrap prerequisites).",
    ),
    "fleet-ip-dhcp0-v4": (
        "Junos relay forwards DHCP from access switches to this address "
        "cluster. Looked up by dns_name prefix dhcp0*, IPv4 family.",
        f"{HOWTO} §0.",
    ),
    "fleet-ip-dhcp0-v6": (
        "Junos DHCPv6 relay target on participant IRBs. The renderer "
        "treats v6 as optional and warns, the dhcpv6 relay block is "
        "omitted from every dist when this is missing.",
        f"{HOWTO} §0.",
    ),
    "fleet-ip-ns0-v4": (
        "Kea Option 6 DNS servers, advertised to access switches via "
        "DHCP. Looked up by dns_name prefix ns0*, IPv4 family.",
        f"{HOWTO} §0.",
    ),
    "fleet-ip-ns0-v6": (
        "IPv6 DNS servers, advertised in IPv6 RA from each participant "
        "IRB. The Junos renderer treats this as optional and warns, the "
        "per IRB dns-server-address lines are omitted when missing.",
        f"{HOWTO} §0.",
    ),
    "fleet-ip-ntp-v4": (
        "Kea Option 42 NTP servers, advertised to access switches via "
        "DHCP. Looked up by dns_name prefix ntp*, IPv4 family.",
        f"{HOWTO} §0.",
    ),
    "fleet-ip-tftp": (
        "TFTP server cluster for access switch bootstrap. Looked up by "
        "dns_name prefix tftp*, IPv4 family.",
        f"{HOWTO} §0.",
    ),
    "fleet-ip-tftp-exact": (
        "Kea reads next-server from this exact dns_name match, the "
        "access switches download access.conf over TFTP from this IP.",
        f"{HOWTO} §0.",
    ),
    "prefix-role": (
        "Both the bootstrap and crew Kea subnets need a Prefix object "
        "with the matching role, the renderer derives subnet, router, "
        "and gateway from the Prefix's network.",
        f"{HOWTO} §0.",
    ),
    "iprange-role": (
        "Both the bootstrap and crew Kea subnets need an IP Range with "
        "the matching role, the renderer reads the DHCP pool bounds "
        "from the Range.",
        f"{HOWTO} §0.",
    ),
    "prefix-role-size": (
        "kea_subnet_from_role computes the router as Prefix network "
        "address + 1. A Prefix smaller than /30 has no usable host "
        "addresses so Kea rejects the rendered subnet at validation "
        "time. The check insists on /30 or larger.",
        f"{HOWTO} §0.",
    ),
    "iprange-bounds-in-prefix": (
        "The bootstrap and crew IP Range start and end addresses must "
        "fall inside the matching Prefix. A range that crosses the "
        "Prefix boundary points Kea at addresses outside the routed "
        "subnet.",
        f"{HOWTO} §0.",
    ),
    "vlan-vid-duplicate": (
        "Two VLAN objects with the same (site, vid) collapse into a "
        "single entry in the renderer's vlans_by_vid index, with the "
        "name flapping between operator edits. The renderer logs a "
        "[warn] for each collision but still emits one of the two "
        "names into the rendered Junos config arbitrarily.",
        f"{HOWTO} §7a + §7c.",
    ),
    "global-ospf-vlan": (
        "The Junos renderer expects a VLAN object per OSPF linknet VID "
        "(1100, 1101, 1200, 1201). Each one's name is written verbatim "
        "into the OSPF IRB description in the rendered config.",
        f"{HOWTO} §7b.",
    ),
    "vlan-name-non-empty": (
        "The renderer copies VLAN.name verbatim into a Junos `set` line. "
        "An empty name fails the dist render at template time.",
        f"{HOWTO} §7a + §7c.",
    ),
    "vlan-name-junos-ident": (
        "The renderer drops VLAN.name verbatim into an unquoted Junos "
        "token (`set groups VLANS vlans <name>` and the IRB description). "
        "Junos requires the identifier to match ^[A-Za-z][A-Za-z0-9_-]*$.",
        f"{HOWTO} §7a + §7c.",
    ),
    "dist-has-site": (
        "Renderer scopes VLAN lookups to the Device's Site, lets each "
        "dist resolve its own site scoped VLAN 600 instead of the "
        "global fallback.",
        f"{HOWTO} §3.",
    ),
    "dist-has-location": (
        "Location.slug becomes the Kea include filename "
        "(`kea-dhcp4-access-<slug>.conf`). Without a Location the Kea "
        "renderer aborts the dist with a missing slug error.",
        f"{HOWTO} §2 + §3.",
    ),
    "dist-slug-unique": (
        "Two dists with the same Location.slug write to the same Kea "
        "include filename, the second overwrites the first. The Kea "
        "renderer surfaces this as a duplicate slug error and aborts.",
        f"{HOWTO} §2.",
    ),
    "dist-iface-lo0": (
        "The renderer reads the dist's loopback IPv4 from interface "
        "lo0.0. Junos itself requires the lowercase name, NetBox accepts "
        "any case so a typo silently drops the loopback IP.",
        f"{HOWTO} §7d.",
    ),
    "dist-lo0-v4": (
        "The Junos renderer hard fails the dist if lo0.0 has no IPv4. "
        "The address is the dist's identity octet, used as the host bit "
        "on the four OSPF linknet IRBs and as the Primary IPv4.",
        f"{HOWTO} §8.",
    ),
    "dist-lo0-prefix32": (
        "Loopback convention is /32, a host route. Other prefix lengths "
        "render through but are out of convention.",
        f"{HOWTO} §8.",
    ),
    "dist-primary-eq-loopback": (
        "Operator convention is to set Device.primary_ip4 to the loopback "
        "address so NetBox UI pages show the right mgmt IP for the dist. "
        "Renderer does not read primary_ip4 but the operator workflow "
        "depends on it.",
        f"{HOWTO} §8 + pitfalls.",
    ),
    "dist-iface-irb600": (
        "irb.600 is the dist's mgmt SVI. Its IPv4 anchors the per dist "
        "mgmt subnet that Kea offers from and the Junos relay agent "
        "binds to.",
        f"{HOWTO} §7d.",
    ),
    "dist-irb600-uv": (
        "Convention is to bind irb.600 to VLAN 600 in NetBox. The "
        "renderer does not require this binding but the IRB-to-VLAN "
        "linkage keeps NetBox internally consistent.",
        f"{HOWTO} §7d.",
    ),
    "dist-irb600-v4": (
        "Kea derives the mgmt /24 subnet from irb.600's IPv4. Without it "
        "the Kea renderer skips the dist with `no IP assigned on irb.600`.",
        f"{HOWTO} §8.",
    ),
    "dist-irb600-prefix24": (
        "Kea expects the mgmt subnet to be /24. The subnet_id derives "
        "from the third octet times 10, a non /24 breaks that math.",
        f"{HOWTO} §8.",
    ),
    "dist-subnet-id-unique": (
        "Kea requires globally unique subnet ids. Two dists sharing the "
        "same third octet of their mgmt /24 produce the same subnet_id, "
        "the Kea renderer aborts with a duplicate id error.",
        f"{HOWTO} §1 + §4.",
    ),
    "dist-irb600-host1": (
        "Convention is .1 of the mgmt /24 for the irb.600 host bit, the "
        "renderer emits this address verbatim as Kea's routers option. "
        "Any other host bit renders through but breaks the gateway "
        "convention.",
        f"{HOWTO} §8.",
    ),
    "dist-iface-ospf-irb": (
        "Each of the four OSPF linknets (1100, 1101, 1200, 1201) needs "
        "an `irb.<vid>` virtual interface on every dist. The renderer "
        "emits OSPF IRB blocks for each one.",
        f"{HOWTO} §7d.",
    ),
    "dist-ospf-uv": (
        "Operator convention is to bind each OSPF IRB to the matching "
        "VLAN id. A mismatch is silently invisible at render time but "
        "leaves NetBox inconsistent with the deployed Junos config.",
        f"{HOWTO} §7d.",
    ),
    "dist-ospf-v4": (
        "OSPF IPv4 sessions need an address on the linknet IRB. The "
        "template gates the v4 family on truthiness so the line is "
        "omitted when missing, the operator should still assign one.",
        f"{HOWTO} §8.",
    ),
    "dist-ospf-v6": (
        "OSPF IPv6 sessions need an address on the linknet IRB. The "
        "template gates the v6 family on truthiness so the line is "
        "omitted when missing, the operator should still assign one.",
        f"{HOWTO} §8.",
    ),
    "dist-mgmt-vlan-scoped": (
        "The Junos renderer reads VLAN.name verbatim into the mgmt VLAN "
        "definition and the irb.600 description. A site scoped VLAN 600 "
        "per dist gives a distinct mgmt VLAN name like "
        "`<DISTRICT>_ACCESS-MGMT`. Falling back to the global VLAN 600 "
        "renders every dist with the same mgmt VLAN name.",
        f"{HOWTO} §7a.",
    ),
    "dist-mgmt-vlan-exists": (
        "Every dist needs a VLAN object with VID 600, site scoped to "
        "the dist's site if possible, otherwise a single global VLAN 600 "
        "covers all dists with one shared name.",
        f"{HOWTO} §7a.",
    ),
    "dist-mgmt-vlan-name": (
        "The renderer fails the dist if the mgmt VLAN 600's name is "
        "empty. Set the Name field on the VLAN object to the desired "
        "Junos identifier.",
        f"{HOWTO} §7a.",
    ),
    "dist-mgmt-prefix-exists": (
        "IPAM tree completeness, every mgmt /24 should be recorded as a "
        "Prefix object. Downstream consumers (humans, audit scripts, "
        "future tooling) walk from VLAN to Prefix to find the mgmt range.",
        f"{HOWTO} §4.",
    ),
    "dist-mgmt-prefix-vlan-bound": (
        "The mgmt /24 Prefix should link to the site scoped VLAN 600 "
        "object. Without the binding, downstream consumers cannot find "
        "the prefix from the VLAN, and IPAM cross referencing breaks.",
        f"{HOWTO} §4 + pitfalls.",
    ),
    "dist-participant-uv": (
        "Each `irb.<vid>` should bind to the matching VID. The renderer "
        "reads the VID from the interface name, a wrong Untagged VLAN "
        "binding renders fine but the dist forwards traffic on the wrong "
        "VLAN once deployed.",
        f"{HOWTO} §7d + pitfalls.",
    ),
    "dist-participant-vlan-exists": (
        "Every participant `irb.<vid>` with an IPv4 needs a NetBox VLAN "
        "object at that vid for the renderer to look up its name. "
        "Without it the dist render fails with `expects VLAN id N`.",
        f"{HOWTO} §7c.",
    ),
    "dist-port-desc": (
        "The dist port description is load bearing. Junos copies it "
        "verbatim into Option 82 circuit id at runtime, Kea hex encodes "
        "it as the host reservation circuit id. The two sides must "
        "agree byte for byte for the access switch to get its IP.",
        f"{HOWTO} §9.",
    ),
    "dist-port-desc-ascii": (
        "Kea encodes the dist port description as ASCII bytes when "
        "building the circuit id, non ASCII characters are rejected at "
        "render time.",
        f"{HOWTO} §9.",
    ),
    "dist-port-mode": (
        "NetBox refuses to attach an Untagged VLAN to a `ge-0/0/N` whose "
        "Mode is unset. The renderer expects mode=access on every cabled "
        "downlink, the howto step 9 sets this before the Untagged VLAN.",
        f"{HOWTO} §9.",
    ),
    "dist-port-uv": (
        "The Junos renderer reads the native VID for each cabled "
        "`ge-0/0/N` from the Untagged VLAN binding. Without it the "
        "render fails the dist.",
        f"{HOWTO} §9.",
    ),
    "access-exists": (
        "At least one device with role access_switch is needed for Kea "
        "to emit reservations. A fleet with zero access switches is "
        "valid but unusual.",
        f"{HOWTO} §6.",
    ),
    "access-vlan600-v4": (
        "netbox2cisco renders the access switch's Vlan600 SVI IPv4 as the "
        "static management address in its TFTP config. Without it the "
        "renderer falls back to primary_ip4, without both the switch is "
        "skipped and gets no config.",
        f"{HOWTO} §6.",
    ),
    "access-vlan600-prefix24": (
        "netbox2cisco derives the dotted subnet mask and the default "
        "gateway (the .1 of the network) from this address's prefix. A "
        "bare address with no prefix, or a prefix other than /24, leaves "
        "the static config with no mask or a wrong gateway.",
        f"{HOWTO} §6.",
    ),
    "access-gateway-derivable": (
        "The default gateway in the static config is the .1 of the "
        "switch's own Vlan600 /24, which by the per district convention "
        "is the uplink dist's irb.600. A switch whose /24 does not match "
        "its uplink dist's mgmt /24 (a flat vs per district misallocation) "
        "would render a gateway that is not on its segment.",
        f"{HOWTO} §6 + §8.",
    ),
    "access-config-filename": (
        "netbox2cisco writes the static config as <name>.conf lowercased "
        "and netbox2kea serves that exact name as the boot-file-name, so "
        "Device.name must be a legal Cisco IOS hostname and a TFTP safe "
        "filename. An illegal name breaks the rendered hostname line or "
        "the TFTP fetch.",
        f"{HOWTO} §6.",
    ),
    "access-vlan600-iface": (
        "Operator convention is to create a Vlan600 SVI on every access "
        "switch and assign the mgmt IP there. primary_ip4 is the "
        "fallback path, not the preferred one.",
        f"{HOWTO} §6.",
    ),
    "access-ip-source": (
        "netbox2cisco cannot render a static config without either a "
        "Vlan600 SVI IP or a primary_ip4 (both carry the prefix it needs "
        "for the mask and gateway). The access switch gets no config "
        "file and cannot bring up management.",
        f"{HOWTO} §6.",
    ),
    "access-primary-set": (
        "Operator convention is to set primary_ip4 to the Vlan600 SVI "
        "IP so NetBox UI shows the right mgmt IP at the top of the "
        "device page.",
        f"{HOWTO} §6.",
    ),
    "fleet-has-dists": (
        "At least one device with role distribution_switches must exist "
        "in NetBox for either renderer to emit any output.",
        f"{HOWTO} §3.",
    ),
    "dist-named-missing": (
        "The dist name passed via --dist did not match any device with "
        "role distribution_switches in NetBox.",
        f"{HOWTO} §3.",
    ),
    # Cable walk (verify the renderer's discovery path), see check_cabling.
    "cable-access-uplink": (
        "Each access switch reaches the fleet through a single cable "
        "from its GigabitEthernet0/2 to a `ge-0/0/N` on a dist. The "
        "renderer walks dcim/cables/ to discover this path, a missing "
        "or duplicate cable is the most common operator mistake during "
        "fleet build out and is invisible without this check.",
        f"{HOWTO} §9.",
    ),
    "cable-dist-in-fleet": (
        "Each access switch's uplink cable must terminate on a dist "
        "device the renderer knows about. A cable to a foreign dist "
        "(out of role, decommissioned, in another fleet) means Kea "
        "will not emit a reservation for that access switch.",
        f"{HOWTO} §9.",
    ),
}


class Counter:
    """Track per-check outcomes and print the running checklist."""

    def __init__(self) -> None:
        self.counts = {"OK": 0, "WARN": 0, "FAIL": 0}
        self.fails: list[tuple[str, str, str]] = []  # (item, detail, kind)
        self.warns: list[tuple[str, str, str]] = []

    def check(self, item: str, status: str, detail: str = "",
              *, kind: str) -> None:
        """
        Record one check outcome. `kind` is keyword only and required so
        a missing entry surfaces as a TypeError at call time rather than
        a silent fall through to the generic (no metadata) bucket in the
        summary block.
        """
        tail = f"... {status}"
        if detail:
            tail += f" ({detail})"
        print(f"Checking {item}{tail}")
        self.counts[status] += 1
        if status == "FAIL":
            self.fails.append((item, detail, kind))
        elif status == "WARN":
            self.warns.append((item, detail, kind))

    @staticmethod
    def _emit_block(severity: str,
                    entries: list[tuple[str, str, str]]) -> None:
        """
        Group entries by `kind` and emit one mitigation block per kind.
        Each block lists the affected items plus the Purpose and See
        reference from CHECK_META.

        Per item detail is shown next to each affected line when it
        differs from the bucket's sample detail. Operators screenshot
        the summary, so collapsing every dist's specific mismatched
        value to a single example would lose actionable signal.
        """
        by_kind: dict[str, list[tuple[str, str]]] = defaultdict(list)
        kind_order: list[str] = []
        for item, detail, kind in entries:
            key = kind or "(no metadata)"
            if key not in by_kind:
                kind_order.append(key)
            by_kind[key].append((item, detail))

        for kind in kind_order:
            items = by_kind[kind]
            purpose, see = CHECK_META.get(
                kind, ("(no metadata registered for this check)", "")
            )
            print()
            print(f"  [{severity}] {kind}  ({len(items)} occurrence(s))")
            print(f"    Purpose: {purpose}")
            if see:
                print(f"    See: {see}")
            print("    Affected:")
            sample = next((d for _i, d in items if d), "")
            if sample:
                print(f"      (sample detail: {sample})")
            for item, detail in items:
                if detail and detail != sample:
                    print(f"      - {item}  ({detail})")
                else:
                    print(f"      - {item}")

    def summary(self) -> int:
        print()
        print("=" * 70)
        print(f"SUMMARY: {self.counts['OK']} OK, "
              f"{self.counts['WARN']} WARN, {self.counts['FAIL']} FAIL")
        print("=" * 70)
        if self.fails:
            print(f"\n{len(self.fails)} FAILURE(S), must fix in NetBox "
                  f"before renderers will work.")
            self._emit_block("FAIL", self.fails)
        if self.warns:
            print(f"\n{len(self.warns)} WARNING(S), renderers will run "
                  f"but with degraded output.")
            self._emit_block("WARN", self.warns)
        return 2 if self.fails else 0


def check_fleet(client: NetboxClient, ctr: Counter) -> None:
    print("=" * 70)
    print("FLEET BOOTSTRAP")
    print("=" * 70)

    # IPAM roles
    roles = {r["slug"]: r for r in client.get_all("ipam/roles/")}
    for slug in REQUIRED_ROLES:
        if slug in roles:
            ctr.check(f"IPAM role {slug!r}", "OK", kind="ipam-role")
        else:
            ctr.check(f"IPAM role {slug!r}", "FAIL",
                      "create in NetBox IPAM > Roles", kind="ipam-role")

    # Fleet service IPAddresses
    fleet_kind = {
        ("dhcp0", 4): "fleet-ip-dhcp0-v4",
        ("dhcp0", 6): "fleet-ip-dhcp0-v6",
        ("ns0", 4): "fleet-ip-ns0-v4",
        ("ns0", 6): "fleet-ip-ns0-v6",
        ("ntp", 4): "fleet-ip-ntp-v4",
        ("tftp", 4): "fleet-ip-tftp",
    }
    for prefix, family, severity, purpose in FLEET_IPS:
        ips = client.get_all(f"ipam/ip-addresses/?dns_name__isw={prefix}")
        matched = [
            ip for ip in ips
            if ipaddress.ip_interface(ip["address"]).version == family
        ]
        item = f"fleet IPAddress dns_name~{prefix}* v{family} ({purpose})"
        kind = fleet_kind[(prefix, family)]
        if matched:
            ctr.check(item, "OK", f"{len(matched)} address(es)", kind=kind)
        else:
            ctr.check(item, severity,
                      "no matching IPAddress in NetBox", kind=kind)

    # TFTP exact match
    tftp = client.get_all(f"ipam/ip-addresses/?dns_name={TFTP_DNS}")
    item = f"fleet IPAddress dns_name={TFTP_DNS}"
    if tftp:
        ctr.check(item, "OK", tftp[0]["address"], kind="fleet-ip-tftp-exact")
    else:
        ctr.check(item, "FAIL",
                  f"create IPAddress with dns_name={TFTP_DNS}",
                  kind="fleet-ip-tftp-exact")

    # bootstrap and crew need Prefix + IP Range each, plus the Prefix
    # must be at least /30 so kea_subnet_from_role can derive a usable
    # router address (network + 1) and a pool that fits inside.
    for role_slug in ("kea-bootstrap", "kea-crew"):
        prefixes = client.get_all(f"ipam/prefixes/?role={role_slug}")
        ranges = client.get_all(f"ipam/ip-ranges/?role={role_slug}")
        if prefixes:
            pf = prefixes[0]
            ctr.check(f"Prefix with role {role_slug!r}", "OK",
                      pf["prefix"], kind="prefix-role")
            try:
                pf_net = ipaddress.ip_network(pf["prefix"], strict=False)
                if pf_net.prefixlen > 30:
                    ctr.check(f"Prefix {pf['prefix']!r} is at least /30",
                              "FAIL",
                              f"got /{pf_net.prefixlen}, router computation "
                              f"needs at least two usable hosts",
                              kind="prefix-role-size")
                else:
                    ctr.check(f"Prefix {pf['prefix']!r} is at least /30",
                              "OK", kind="prefix-role-size")
                # IP Range bounds must fall inside the Prefix.
                if ranges:
                    r = ranges[0]
                    rs = ipaddress.ip_interface(r["start_address"]).ip
                    re_ = ipaddress.ip_interface(r["end_address"]).ip
                    if rs in pf_net and re_ in pf_net:
                        ctr.check(
                            f"IP Range {role_slug!r} bounds inside Prefix",
                            "OK", kind="iprange-bounds-in-prefix")
                    else:
                        ctr.check(
                            f"IP Range {role_slug!r} bounds inside Prefix",
                            "FAIL",
                            f"{rs} or {re_} outside {pf_net}",
                            kind="iprange-bounds-in-prefix")
            except (ValueError, KeyError) as exc:
                ctr.check(f"Prefix {pf.get('prefix')!r} is parseable",
                          "FAIL", str(exc), kind="prefix-role-size")
        else:
            ctr.check(f"Prefix with role {role_slug!r}", "FAIL",
                      kind="prefix-role")
        if ranges:
            r = ranges[0]
            ctr.check(f"IP Range with role {role_slug!r}", "OK",
                      f"{r['start_address']} - {r['end_address']}",
                      kind="iprange-role")
        else:
            ctr.check(f"IP Range with role {role_slug!r}", "FAIL",
                      kind="iprange-role")


def check_global_vlans(client: NetboxClient, vlan_index: dict,
                       ctr: Counter) -> None:
    print()
    print("=" * 70)
    print("GLOBAL VLANS")
    print("=" * 70)
    # Duplicate (site_id, vid) detection. vlans_by_vid collapses
    # duplicates silently, the raw NetBox response is the truth source.
    raw = client.get_all("ipam/vlans/")
    seen: dict[tuple, list[str]] = defaultdict(list)
    for v in raw:
        vid = v.get("vid")
        if vid is None:
            continue
        site_id = (v.get("site") or {}).get("id")
        seen[(site_id, vid)].append(v.get("name") or "<unnamed>")
    for (site_id, vid), names in seen.items():
        if len(names) > 1:
            ctr.check(
                f"VLAN (site={site_id}, vid={vid}) has unique definition",
                "FAIL",
                f"{len(names)} objects share this key: {names}",
                kind="vlan-vid-duplicate",
            )
    vids = {vid for (_site, vid) in vlan_index}
    for vid in OSPF_VIDS:
        if vid in vids:
            ctr.check(f"VLAN id {vid} exists in NetBox (OSPF linknet)",
                      "OK", kind="global-ospf-vlan")
            # The renderer copies the VLAN name verbatim into a Junos
            # `set` line, validate the identifier shape here too.
            vlan = (vlan_index.get((None, vid))
                    or next((v for (s, vd), v in vlan_index.items()
                             if vd == vid), None))
            vname = (vlan or {}).get("name") or ""
            if not vname:
                ctr.check(f"VLAN vid={vid} has non empty name",
                          "FAIL", "renderer copies name into Junos config",
                          kind="vlan-name-non-empty")
            elif not JUNOS_IDENT_RE.match(vname):
                ctr.check(f"VLAN vid={vid} name is Junos identifier",
                          "FAIL",
                          f"{vname!r} fails ^[A-Za-z][A-Za-z0-9_-]*$",
                          kind="vlan-name-junos-ident")
            else:
                ctr.check(f"VLAN vid={vid} name is Junos identifier",
                          "OK", f"name={vname!r}",
                          kind="vlan-name-junos-ident")
        else:
            ctr.check(f"VLAN id {vid} exists in NetBox (OSPF linknet)",
                      "FAIL", f"create VLAN with vid={vid}",
                      kind="global-ospf-vlan")


def check_dist(client: NetboxClient, dist: dict, vlan_index: dict,
               slug_seen: dict, subnet_id_seen: dict,
               ctr: Counter) -> None:
    name = dist["name"]
    print()
    print(f"--- {name} ---")

    # Site
    site = dist.get("site")
    site_id = (site or {}).get("id")
    if site:
        ctr.check(f"{name} has Site", "OK", site["slug"], kind="dist-has-site")
    else:
        ctr.check(f"{name} has Site", "FAIL",
                  "VLAN site scoping needs the Device's Site",
                  kind="dist-has-site")

    # Location and slug uniqueness
    loc = dist.get("location")
    if loc:
        slug = loc.get("slug")
        ctr.check(f"{name} has Location", "OK", slug,
                  kind="dist-has-location")
        if slug:
            if slug in slug_seen:
                ctr.check(f"{name} Location.slug unique", "FAIL",
                          f"{slug!r} also used by {slug_seen[slug]}",
                          kind="dist-slug-unique")
            else:
                slug_seen[slug] = name
                ctr.check(f"{name} Location.slug unique", "OK",
                          kind="dist-slug-unique")
    else:
        ctr.check(f"{name} has Location", "FAIL",
                  "Kea filename derives from Location.slug",
                  kind="dist-has-location")

    # Interfaces and IPs
    ifs = client.get_all(f"dcim/interfaces/?device_id={dist['id']}")
    by_name = {i["name"]: i for i in ifs}
    ips = client.get_all(f"ipam/ip-addresses/?device_id={dist['id']}")
    ips_by_iface: dict[str, list[str]] = defaultdict(list)
    for ip in ips:
        ao = ip.get("assigned_object") or {}
        if ao.get("name"):
            ips_by_iface[ao["name"]].append(ip["address"])

    # lo0.0 — Junos hard requirement
    lo_name = "lo0.0"
    if lo_name in by_name:
        ctr.check(f"{name} has interface lo0.0", "OK", kind="dist-iface-lo0")
    else:
        wrong_case = next(
            (n for n in by_name if n.lower() == lo_name), None
        )
        if wrong_case:
            ctr.check(f"{name} has interface lo0.0", "WARN",
                      f"found as {wrong_case!r}, "
                      f"Junos requires lowercase, rename in NetBox",
                      kind="dist-iface-lo0")
            lo_name = wrong_case
        else:
            ctr.check(f"{name} has interface lo0.0", "FAIL",
                      "create lo0.0 (type=virtual) and assign /32",
                      kind="dist-iface-lo0")
            lo_name = None
    loopback_addr = None
    if lo_name:
        v4 = [a for a in ips_by_iface.get(lo_name, [])
              if ipaddress.ip_interface(a).version == 4]
        if v4:
            ctr.check(f"{name} {lo_name} has IPv4", "OK", v4[0],
                      kind="dist-lo0-v4")
            loopback_addr = ipaddress.ip_interface(v4[0])
            # howto step 8, loopback prefix is /32
            if loopback_addr.network.prefixlen == 32:
                ctr.check(f"{name} {lo_name} IPv4 prefix is /32", "OK",
                          kind="dist-lo0-prefix32")
            else:
                ctr.check(f"{name} {lo_name} IPv4 prefix is /32", "WARN",
                          f"got /{loopback_addr.network.prefixlen}, "
                          f"convention is /32 for a host route loopback",
                          kind="dist-lo0-prefix32")
        else:
            ctr.check(f"{name} {lo_name} has IPv4", "FAIL",
                      "renderer hard fails without a loopback IP",
                      kind="dist-lo0-v4")

    # howto step 8, Primary IPv4 should point at the loopback
    primary = (dist.get("primary_ip4") or {}).get("address")
    if primary and loopback_addr:
        if primary == str(loopback_addr):
            ctr.check(f"{name} Primary IPv4 = loopback IP", "OK",
                      kind="dist-primary-eq-loopback")
        else:
            ctr.check(f"{name} Primary IPv4 = loopback IP", "WARN",
                      f"Primary IPv4={primary}, loopback={loopback_addr}",
                      kind="dist-primary-eq-loopback")
    elif not primary:
        ctr.check(f"{name} Primary IPv4 set", "WARN",
                  "convention is to set Primary IPv4 to the loopback IP",
                  kind="dist-primary-eq-loopback")

    # irb.600 — mgmt subnet anchor
    mgmt_net = None
    irb600 = by_name.get("irb.600")
    if irb600:
        ctr.check(f"{name} has interface irb.600", "OK",
                  kind="dist-iface-irb600")
        irb600_uv = (irb600.get("untagged_vlan") or {}).get("vid")
        if irb600_uv == MGMT_VID:
            ctr.check(f"{name} irb.600 untagged_vlan=600", "OK",
                      kind="dist-irb600-uv")
        elif irb600_uv is None:
            ctr.check(f"{name} irb.600 untagged_vlan=600", "WARN",
                      "no Untagged VLAN bound, NetBox accepts this but "
                      "operator convention is to bind to VLAN 600",
                      kind="dist-irb600-uv")
        else:
            ctr.check(f"{name} irb.600 untagged_vlan=600", "FAIL",
                      f"bound to vid {irb600_uv}, expected 600",
                      kind="dist-irb600-uv")
        v4 = [a for a in ips_by_iface.get("irb.600", [])
              if ipaddress.ip_interface(a).version == 4]
        if v4:
            addr = ipaddress.ip_interface(v4[0])
            ctr.check(f"{name} irb.600 has IPv4", "OK", str(addr),
                      kind="dist-irb600-v4")
            if addr.network.prefixlen == 24:
                ctr.check(f"{name} irb.600 IPv4 prefix is /24", "OK",
                          kind="dist-irb600-prefix24")
                mgmt_net = addr.network
                octet3 = int(str(mgmt_net.network_address).split(".")[2])
                subnet_id = octet3 * 10
                if subnet_id in subnet_id_seen:
                    ctr.check(f"{name} Kea subnet_id {subnet_id} unique",
                              "FAIL",
                              f"collides with {subnet_id_seen[subnet_id]}",
                              kind="dist-subnet-id-unique")
                else:
                    subnet_id_seen[subnet_id] = name
                    ctr.check(f"{name} Kea subnet_id {subnet_id} unique",
                              "OK", kind="dist-subnet-id-unique")
                if int(str(addr.ip).split(".")[-1]) == 1:
                    ctr.check(f"{name} irb.600 host bit is .1", "OK",
                              kind="dist-irb600-host1")
                else:
                    ctr.check(f"{name} irb.600 host bit is .1", "WARN",
                              f"got .{str(addr.ip).split('.')[-1]}, "
                              f"convention is .1",
                              kind="dist-irb600-host1")
            else:
                ctr.check(f"{name} irb.600 IPv4 prefix is /24", "FAIL",
                          f"got /{addr.network.prefixlen}",
                          kind="dist-irb600-prefix24")
        else:
            ctr.check(f"{name} irb.600 has IPv4", "FAIL",
                      "Kea derives mgmt subnet from this address",
                      kind="dist-irb600-v4")
    else:
        ctr.check(f"{name} has interface irb.600", "FAIL",
                  "needed for mgmt SVI", kind="dist-iface-irb600")

    # OSPF IRBs
    for vid in OSPF_VIDS:
        iname = f"irb.{vid}"
        if iname not in by_name:
            ctr.check(f"{name} has interface {iname}", "FAIL",
                      f"OSPF linknet {vid} needs an IRB",
                      kind="dist-iface-ospf-irb")
            continue
        ctr.check(f"{name} has interface {iname}", "OK",
                  kind="dist-iface-ospf-irb")
        uv_vid = (by_name[iname].get("untagged_vlan") or {}).get("vid")
        if uv_vid == vid:
            ctr.check(f"{name} {iname} untagged_vlan={vid}", "OK",
                      kind="dist-ospf-uv")
        elif uv_vid is None:
            ctr.check(f"{name} {iname} untagged_vlan={vid}", "WARN",
                      "no Untagged VLAN bound, convention is to bind to "
                      "the matching VID", kind="dist-ospf-uv")
        else:
            ctr.check(f"{name} {iname} untagged_vlan={vid}", "FAIL",
                      f"bound to vid {uv_vid}, expected {vid}",
                      kind="dist-ospf-uv")
        v4 = [a for a in ips_by_iface.get(iname, [])
              if ipaddress.ip_interface(a).version == 4]
        v6 = [a for a in ips_by_iface.get(iname, [])
              if ipaddress.ip_interface(a).version == 6]
        if v4:
            ctr.check(f"{name} {iname} has IPv4", "OK", v4[0],
                      kind="dist-ospf-v4")
        else:
            ctr.check(f"{name} {iname} has IPv4", "WARN", "OSPF expects v4",
                      kind="dist-ospf-v4")
        if v6:
            ctr.check(f"{name} {iname} has IPv6", "OK", v6[0],
                      kind="dist-ospf-v6")
        else:
            ctr.check(f"{name} {iname} has IPv6", "WARN", "OSPF expects v6",
                      kind="dist-ospf-v6")

    # Mgmt VLAN 600, site scoped check
    scoped = vlan_index.get((site_id, MGMT_VID))
    glob = vlan_index.get((None, MGMT_VID))
    chosen = scoped or glob
    if scoped:
        ctr.check(f"{name} mgmt VLAN 600 site scoped", "OK",
                  f"name={scoped.get('name')!r}",
                  kind="dist-mgmt-vlan-scoped")
    elif glob:
        ctr.check(f"{name} mgmt VLAN 600 site scoped", "WARN",
                  f"falls back to global {glob.get('name')!r}, "
                  f"all dists get same mgmt VLAN name",
                  kind="dist-mgmt-vlan-scoped")
    else:
        ctr.check(f"{name} mgmt VLAN 600 exists", "FAIL",
                  "create VLAN with vid=600",
                  kind="dist-mgmt-vlan-exists")
    if chosen:
        vname = chosen.get("name") or ""
        if not vname:
            ctr.check(f"{name} mgmt VLAN 600 has non empty name",
                      "FAIL", "renderer copies name into Junos config",
                      kind="dist-mgmt-vlan-name")
        elif not JUNOS_IDENT_RE.match(vname):
            ctr.check(f"{name} mgmt VLAN 600 name is Junos identifier",
                      "FAIL",
                      f"{vname!r} fails ^[A-Za-z][A-Za-z0-9_-]*$",
                      kind="vlan-name-junos-ident")
        else:
            ctr.check(f"{name} mgmt VLAN 600 name is Junos identifier",
                      "OK", kind="vlan-name-junos-ident")

    # Mgmt /24 Prefix in IPAM and VLAN binding, howto step 4 + pitfall
    if mgmt_net:
        prefixes = client.get_all(f"ipam/prefixes/?prefix={mgmt_net}")
        if prefixes:
            ctr.check(f"{name} mgmt /24 Prefix {mgmt_net} in IPAM",
                      "OK", f"id={prefixes[0]['id']}",
                      kind="dist-mgmt-prefix-exists")
            pf_vlan = prefixes[0].get("vlan") or {}
            pf_vid = pf_vlan.get("vid")
            if pf_vid == MGMT_VID:
                ctr.check(
                    f"{name} mgmt /24 Prefix bound to VLAN 600", "OK",
                    f"name={pf_vlan.get('name')!r}",
                    kind="dist-mgmt-prefix-vlan-bound",
                )
            elif pf_vid is None:
                ctr.check(f"{name} mgmt /24 Prefix bound to VLAN 600",
                          "WARN", "no VLAN binding, see pitfall",
                          kind="dist-mgmt-prefix-vlan-bound")
            else:
                ctr.check(f"{name} mgmt /24 Prefix bound to VLAN 600",
                          "FAIL", f"bound to vid {pf_vid}, expected 600",
                          kind="dist-mgmt-prefix-vlan-bound")
        else:
            ctr.check(f"{name} mgmt /24 Prefix {mgmt_net} in IPAM",
                      "FAIL", "create Prefix per howto step 4",
                      kind="dist-mgmt-prefix-exists")

    # kea-dist-mgmt IP Range check, removed in the H3 remediation.
    # The new static config model means access switches no longer DHCP
    # on VLAN 600, Kea defines no mgmt subnet and reads no mgmt pool,
    # so the role and its per dist IP Ranges are residual state with
    # no active consumer. The verify treats their presence or absence
    # as out of scope rather than emitting a WARN that the operator
    # cannot act on. The historical `kea_dist_pool_for_subnet` helper
    # in netbox_common still reads the role for the bootstrap scripts
    # that touched the data during the 2026 migration.

    # Participant IRBs and their VLAN names
    for iname in by_name:
        m = IRB_RE.match(iname)
        if not m:
            continue
        vid = int(m.group(1))
        if vid in INFRASTRUCTURE_VIDS:
            continue
        v4 = [a for a in ips_by_iface.get(iname, [])
              if ipaddress.ip_interface(a).version == 4]
        if not v4:
            # Parked IRB, renderer skips it, no check needed.
            continue
        # Participant IRB's untagged_vlan must match the irb.<vid> VID
        uv_vid = (by_name[iname].get("untagged_vlan") or {}).get("vid")
        if uv_vid == vid:
            ctr.check(f"{name} {iname} untagged_vlan={vid}", "OK",
                      kind="dist-participant-uv")
        elif uv_vid is None:
            ctr.check(f"{name} {iname} untagged_vlan={vid}", "WARN",
                      "no Untagged VLAN bound, set to matching VID",
                      kind="dist-participant-uv")
        else:
            ctr.check(f"{name} {iname} untagged_vlan={vid}", "FAIL",
                      f"bound to vid {uv_vid}, expected {vid}",
                      kind="dist-participant-uv")
        vlan_obj = (vlan_index.get((site_id, vid))
                    or vlan_index.get((None, vid)))
        if not vlan_obj:
            ctr.check(f"{name} {iname} (vid={vid}) has NetBox VLAN object",
                      "FAIL", f"create VLAN with vid={vid}",
                      kind="dist-participant-vlan-exists")
            continue
        ctr.check(f"{name} {iname} (vid={vid}) has NetBox VLAN object",
                  "OK", f"name={vlan_obj.get('name')!r}",
                  kind="dist-participant-vlan-exists")
        vname = vlan_obj.get("name") or ""
        if not vname:
            ctr.check(f"{name} VLAN vid={vid} has non empty name",
                      "FAIL", "renderer copies name into Junos config",
                      kind="vlan-name-non-empty")
        elif not JUNOS_IDENT_RE.match(vname):
            ctr.check(f"{name} VLAN vid={vid} name is Junos identifier",
                      "FAIL", f"{vname!r} fails ^[A-Za-z][A-Za-z0-9_-]*$",
                      kind="vlan-name-junos-ident")
        else:
            ctr.check(f"{name} VLAN vid={vid} name is Junos identifier",
                      "OK", kind="vlan-name-junos-ident")

    # Cabled ge-0/0/N ports
    cabled = [(n, i) for n, i in by_name.items()
              if GE_RE.match(n) and i.get("cable")]
    for pname, port in sorted(cabled,
                              key=lambda kv: int(kv[0].split("/")[-1])):
        desc = (port.get("description") or "").strip()
        mode = (port.get("mode") or {}).get("value")
        uv_vid = (port.get("untagged_vlan") or {}).get("vid")
        if desc:
            ctr.check(f"{name} {pname} has description", "OK", desc,
                      kind="dist-port-desc")
            try:
                desc.encode("ascii")
                ctr.check(f"{name} {pname} description is ASCII", "OK",
                          kind="dist-port-desc-ascii")
            except UnicodeEncodeError:
                ctr.check(f"{name} {pname} description is ASCII",
                          "FAIL", "Kea hex encodes ASCII only",
                          kind="dist-port-desc-ascii")
        else:
            ctr.check(f"{name} {pname} has description", "FAIL",
                      "Junos and Kea both need this",
                      kind="dist-port-desc")
        if mode == "access":
            ctr.check(f"{name} {pname} mode=access", "OK",
                      kind="dist-port-mode")
        else:
            ctr.check(f"{name} {pname} mode=access", "FAIL",
                      f"current mode={mode!r}, NetBox refuses untagged_vlan "
                      f"without access mode", kind="dist-port-mode")
        if uv_vid is not None:
            ctr.check(f"{name} {pname} has untagged_vlan", "OK",
                      f"vid={uv_vid}", kind="dist-port-uv")
        else:
            ctr.check(f"{name} {pname} has untagged_vlan", "FAIL",
                      "Junos reads native VID from this binding",
                      kind="dist-port-uv")


def check_cabling(client: NetboxClient, ctr: Counter,
                  dist_ids: set[int],
                  scope_dist_ids: set[int] | None = None
                  ) -> tuple[set[int], dict[int, int]]:
    """
    Walk dcim/cables/ and report per access switch. Each access switch
    must have exactly one cable from its GigabitEthernet0/2 to a
    `ge-0/0/N` on a dist in the fleet, the renderer's cable discovery
    expects this shape.

    Returns a tuple of (in_scope, uplink_dist_id_by_access). `in_scope`
    is the set of access device ids whose uplink lands on a dist in
    `scope_dist_ids` (or any dist when scope is None), so the caller can
    scope subsequent access switch checks. `uplink_dist_id_by_access`
    maps each access device id to its uplink dist device id, used by the
    access checks to resolve the per district gateway.
    """
    print()
    print("=" * 70)
    print("CABLES (access uplinks)")
    print("=" * 70)

    cables = client.get_all("dcim/cables/")
    access_devices = client.get_all(f"dcim/devices/?role={ROLE_ACCESS}")
    access_by_id = {d["id"]: d for d in access_devices}

    # The cable walk is shared with the renderers via access_uplinks, so
    # verify and render discover the same edges. The helper returns the
    # dist port object and the dist device per uplink.
    uplinks_by_access = access_uplinks(cables, access_by_id)

    in_scope: set[int] = set()
    uplink_dist_id_by_access: dict[int, int] = {}
    for dev_id, access_dev in sorted(access_by_id.items(),
                                     key=lambda kv: kv[1]["name"]):
        name = access_dev["name"]
        uplinks = uplinks_by_access.get(dev_id, [])
        if len(uplinks) == 0:
            ctr.check(f"{name} has uplink cable to a dist", "FAIL",
                      "no cable terminates this access switch",
                      kind="cable-access-uplink")
            continue
        if len(uplinks) > 1:
            ctr.check(f"{name} has exactly one uplink cable", "WARN",
                      f"{len(uplinks)} cables terminate this access switch",
                      kind="cable-access-uplink")
            # First match still drives the in_scope decision.
        far_obj, far_dev = uplinks[0]
        far_dev_id = far_dev.get("id")
        far_name = far_obj.get("name", "")
        if far_dev_id not in dist_ids:
            ctr.check(f"{name} uplink lands on a dist in the fleet", "FAIL",
                      f"far end device id {far_dev_id} is not a dist, "
                      f"port {far_name!r}",
                      kind="cable-dist-in-fleet")
            continue
        if not GE_RE.match(far_name):
            ctr.check(f"{name} uplink lands on a `ge-0/0/N` port", "WARN",
                      f"far end port is {far_name!r}, expected ge-0/0/N",
                      kind="cable-access-uplink")
            # Treat as in scope, the renderer's port iteration handles
            # the shape via its own regex and would skip non matches.
        else:
            ctr.check(f"{name} has uplink cable to a dist", "OK",
                      f"-> {far_name}", kind="cable-access-uplink")
        uplink_dist_id_by_access[dev_id] = far_dev_id
        if scope_dist_ids is None or far_dev_id in scope_dist_ids:
            in_scope.add(dev_id)

    return in_scope, uplink_dist_id_by_access


def check_access(client: NetboxClient, ctr: Counter,
                 scope_device_ids: set[int] | None = None,
                 *,
                 uplink_dist_id_by_access: dict[int, int] | None = None,
                 dist_gateway_by_id: dict[int, str] | None = None) -> None:
    """
    Per access switch checks. The Vlan600 lookup mirrors the renderer's
    bulk fetch pattern (see netbox2kea.py collect_reservations), one
    bulk query for all access devices' IPs, then group on
    assigned_object.name. This keeps verify and render reading the same
    data and cuts per device round trips for larger fleets.

    `scope_device_ids` limits checks to the named access devices,
    typically populated from the cable walk when --dist is in use.
    """
    print()
    print("=" * 70)
    print("ACCESS SWITCHES")
    print("=" * 70)
    devices = sorted(client.get_all(f"dcim/devices/?role={ROLE_ACCESS}"),
                     key=lambda d: d["name"])
    if scope_device_ids is not None:
        devices = [d for d in devices if d["id"] in scope_device_ids]
    if not devices:
        ctr.check(f"access switches exist (role={ROLE_ACCESS!r})", "WARN",
                  "no access switches in NetBox", kind="access-exists")
        return
    ctr.check(f"access switches exist (role={ROLE_ACCESS!r})", "OK",
              f"{len(devices)} found", kind="access-exists")

    # Bulk fetch interfaces and IPs for every access device in scope,
    # one round trip each, group on (device_id, iface_name). Mirrors
    # netbox2kea.py:125 so verify and render see the same address.
    device_ids = [d["id"] for d in devices]
    ids_param = ",".join(str(i) for i in device_ids)
    all_ifs = client.get_all(
        f"dcim/interfaces/?device_id__in={ids_param}"
    )
    vlan600_by_dev: dict[int, dict] = {}
    for i in all_ifs:
        dev = i.get("device") or {}
        if dev.get("id") in device_ids and i["name"] == "Vlan600":
            vlan600_by_dev[dev["id"]] = i
    all_ips = client.get_all(
        f"ipam/ip-addresses/?device_id__in={ids_param}"
    )
    v600_v4_by_dev: dict[int, str] = {}
    for ip in all_ips:
        ao = ip.get("assigned_object") or {}
        if ao.get("name") != "Vlan600":
            continue
        dev = ao.get("device") or {}
        if (dev.get("id") in device_ids
                and ipaddress.ip_interface(ip["address"]).version == 4):
            # First v4 wins, mirrors renderer's first-match behaviour.
            v600_v4_by_dev.setdefault(dev["id"], ip["address"])

    for d in devices:
        name = d["name"]
        primary = d.get("primary_ip4")
        has_iface = d["id"] in vlan600_by_dev
        v600_v4 = v600_v4_by_dev.get(d["id"])

        if has_iface:
            ctr.check(f"{name} has Vlan600 SVI interface", "OK",
                      kind="access-vlan600-iface")
            if v600_v4:
                ctr.check(f"{name} has Vlan600 with IPv4", "OK",
                          v600_v4, kind="access-vlan600-v4")
            elif primary:
                ctr.check(f"{name} has Vlan600 with IPv4", "WARN",
                          f"Vlan600 has no IP, Kea will fall back to "
                          f"primary_ip4 {primary['address']}",
                          kind="access-vlan600-v4")
            else:
                ctr.check(f"{name} has Vlan600 with IPv4", "FAIL",
                          "Kea cannot derive reservation IP",
                          kind="access-ip-source")
        elif primary:
            ctr.check(f"{name} has Vlan600 SVI interface", "WARN",
                      f"no Vlan600, Kea will use primary_ip4 "
                      f"{primary['address']}", kind="access-vlan600-iface")
        else:
            ctr.check(f"{name} has Vlan600 with IPv4 or primary_ip4",
                      "FAIL", "Kea cannot derive reservation IP",
                      kind="access-ip-source")

        if not primary:
            ctr.check(f"{name} primary_ip4 set", "WARN",
                      "convention is to set primary_ip4 to the Vlan600 IP",
                      kind="access-primary-set")

        # netbox2cisco specifics, the management address must be a /24 so
        # the renderer can derive the dotted mask and the .1 gateway, and
        # the gateway should match the uplink dist's irb.600 (per district
        # addressing). The mgmt address is the Vlan600 IPv4, falling back
        # to primary_ip4, the same source order netbox2cisco uses.
        mgmt_addr = v600_v4 or (primary or {}).get("address")
        if mgmt_addr:
            try:
                iface = ipaddress.ip_interface(mgmt_addr)
            except ValueError:
                ctr.check(f"{name} mgmt address parses as an IP", "FAIL",
                          f"{mgmt_addr!r} is not a valid address",
                          kind="access-vlan600-prefix24")
                iface = None
            if iface is not None and iface.version != 4:
                ctr.check(f"{name} mgmt address is IPv4", "FAIL",
                          f"{mgmt_addr} is IPv6", kind="access-vlan600-prefix24")
            elif iface is not None and iface.network.prefixlen != 24:
                ctr.check(f"{name} mgmt address is a /24", "FAIL",
                          f"{mgmt_addr} is /{iface.network.prefixlen}, "
                          f"netbox2cisco needs a /24 for the mask and gateway",
                          kind="access-vlan600-prefix24")
            elif iface is not None:
                ctr.check(f"{name} mgmt address is a /24", "OK", mgmt_addr,
                          kind="access-vlan600-prefix24")
                gw = str(iface.network.network_address + 1)
                dist_id = (uplink_dist_id_by_access or {}).get(d["id"])
                dist_gw = (dist_gateway_by_id or {}).get(dist_id)
                if dist_gw is None:
                    ctr.check(f"{name} gateway {gw} matches uplink dist "
                              f"irb.600", "WARN",
                              "uplink dist mgmt gateway not resolvable, "
                              "gateway taken as .1 of the switch /24",
                              kind="access-gateway-derivable")
                elif dist_gw != gw:
                    ctr.check(f"{name} gateway {gw} matches uplink dist "
                              f"irb.600", "WARN",
                              f"uplink dist irb.600 is {dist_gw}, the switch "
                              f"/24 .1 is {gw}, per district mismatch",
                              kind="access-gateway-derivable")
                else:
                    ctr.check(f"{name} gateway {gw} matches uplink dist "
                              f"irb.600", "OK", gw,
                              kind="access-gateway-derivable")

        # The Device name becomes the IOS hostname and, lowercased, the
        # TFTP boot file name netbox2kea serves, so it must be a legal IOS
        # identifier.
        if IOS_HOSTNAME_RE.match(name):
            ctr.check(f"{name} renders a TFTP safe boot file", "OK",
                      access_config_filename(d), kind="access-config-filename")
        else:
            ctr.check(f"{name} is a legal IOS hostname", "FAIL",
                      f"{name!r} must match {IOS_HOSTNAME_RE.pattern}",
                      kind="access-config-filename")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist", metavar="NAME",
        help="Limit per dist checks to a single dist by Device.name.",
    )
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()
    ctr = Counter()

    check_fleet(client, ctr)

    vlan_index = vlans_by_vid(client)
    check_global_vlans(client, vlan_index, ctr)

    print()
    print("=" * 70)
    print("DIST DEVICES")
    print("=" * 70)
    dists = sorted(client.get_all(f"dcim/devices/?role={ROLE_DIST}"),
                   key=lambda d: d["name"])
    if args.dist:
        dists = [d for d in dists if d["name"] == args.dist]
        if not dists:
            ctr.check(f"dist {args.dist!r} exists", "FAIL",
                      "not found in NetBox", kind="dist-named-missing")
            return ctr.summary()
    if not dists:
        ctr.check(f"dist devices exist (role={ROLE_DIST!r})", "FAIL",
                  "no dist devices in NetBox", kind="fleet-has-dists")
        return ctr.summary()
    ctr.check(f"dist devices exist (role={ROLE_DIST!r})", "OK",
              f"{len(dists)} found", kind="fleet-has-dists")

    slug_seen: dict[str, str] = {}
    subnet_id_seen: dict[int, str] = {}
    for d in dists:
        check_dist(client, d, vlan_index,
                   slug_seen, subnet_id_seen, ctr)

    # Cable walk validates against every dist with role
    # `distribution_switches`, the --dist filter only narrows which
    # access switches roll forward into the per access checks. Under
    # --dist, the in_scope return is the set of access switches whose
    # uplink lands on the named dist, so per access checks read only
    # those. Without --dist, in_scope covers every access switch.
    all_dists = sorted(client.get_all(f"dcim/devices/?role={ROLE_DIST}"),
                       key=lambda d: d["name"])
    all_dist_ids = {d["id"] for d in all_dists}
    scope_dist_ids = {d["id"] for d in dists}
    in_scope, uplink_dist_id_by_access = check_cabling(
        client, ctr, dist_ids=all_dist_ids, scope_dist_ids=scope_dist_ids)

    # Resolve each dist's mgmt gateway (the irb.600 IP) so the per access
    # checks can cross check that a switch's .1 gateway matches its uplink
    # dist. Best effort, a dist whose info cannot be resolved simply skips
    # the cross check for the switches behind it.
    dist_gateway_by_id: dict[int, str] = {}
    for d in all_dists:
        try:
            dist_gateway_by_id[d["id"]] = dist_info_from_device(
                client, d)["mgmt_gateway"]
        except RuntimeError:
            pass

    check_access(client, ctr, scope_device_ids=in_scope,
                 uplink_dist_id_by_access=uplink_dist_id_by_access,
                 dist_gateway_by_id=dist_gateway_by_id)

    return ctr.summary()


if __name__ == "__main__":
    sys.exit(main())
