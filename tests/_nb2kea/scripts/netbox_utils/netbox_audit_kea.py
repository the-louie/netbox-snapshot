#!/usr/bin/env python3
"""
Inspect NetBox for the data points that `netbox2kea.py` currently hard codes.

Read only. The script makes no changes, it dumps a structured report so the
operator and the renderer maintainer can decide which constants can move
from the script into NetBox.

For each constant `netbox2kea.py` needs, the script checks the most likely
NetBox home, prints what it finds, and suggests the smallest schema change
needed if the data is missing.

The sections covered are,
  1. Tags, the existing set, with focus on any kea, dns, ntp, or tftp tags.
  2. Custom fields on Prefix and IP Address.
  3. Prefix roles, since NetBox separates Device Roles from Prefix Roles.
  4. The bootstrap Prefix, 92.33.43.192/26.
  5. The crew Prefix, 92.33.58.96/27.
  6. IP Ranges inside the bootstrap and crew Prefixes (for dynamic pools).
  7. IP Addresses matching the known service IPs, DNS, NTP, TFTP, DHCP.
  8. IPAM Services that mention DNS, NTP, or TFTP.
  9. Devices and VMs whose name suggests a service host.
 10. Config Contexts attached to Site Hall D (a likely home for domain name).

Usage,
    export NB_TOKEN="..."
    ./netbox_audit_kea.py
    ./netbox_audit_kea.py --json /tmp/nb-kea-audit.json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from netbox_common import NetboxClient, require_token

# The constants this script is auditing for. Each entry pairs a label with
# the IP, prefix, or hostname pattern to look up.
KNOWN_PREFIXES = {
    "bootstrap": "92.33.43.192/26",
    "crew":      "92.33.58.96/27",
}

KNOWN_SERVICE_IPS = {
    "dns_primary":     "92.33.56.5",
    "dns_secondary":   "92.33.56.6",
    "dhcp_primary":    "92.33.56.44",
    "dhcp_secondary":  "92.33.56.45",
    "tftp":            "92.33.56.46",
    "ntp_external":    "194.58.200.20",
}

KNOWN_SERVICE_HOSTNAME_FRAGMENTS = (
    "dns", "ns0", "ns01", "ns02",
    "ntp",
    "tftp",
    "dhcp",
)


# ---------------------------------------------------------------------------
# Output helpers, mirroring netbox_audit.py for visual consistency.
# ---------------------------------------------------------------------------

def header(line: str) -> None:
    print()
    print("=" * 72)
    print(f"  {line}")
    print("=" * 72)


def ok(line: str) -> None:
    print(f"  [ ok ]    {line}")


def warn(line: str) -> None:
    print(f"  [warn]    {line}")


def miss(line: str) -> None:
    print(f"  [MISS]    {line}")


def info(line: str) -> None:
    print(f"           {line}")


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def audit_tags(raw: dict, client: NetboxClient) -> None:
    header("1.  Tags relevant to Kea constants")
    tags = client.get_all("extras/tags/")
    raw["tags"] = tags
    slugs = {t["slug"] for t in tags}
    info(f"{len(tags)} tags total, slugs, {sorted(slugs)}")
    suggestive = sorted(s for s in slugs
                        if any(k in s.lower()
                               for k in ("kea", "dns", "ntp", "tftp", "dhcp",
                                         "bootstrap", "crew", "service")))
    if suggestive:
        ok(f"tags that look relevant, {suggestive}")
    else:
        miss("no kea, dns, ntp, tftp, dhcp, bootstrap, crew, or service tag exists yet")
        info("suggested tags to add for clean Kea sourcing,")
        info("  kea-bootstrap-prefix, kea-crew-prefix,")
        info("  service-dns, service-ntp, service-tftp")


def audit_custom_fields(raw: dict, client: NetboxClient) -> None:
    header("2.  Custom fields on Prefix and IP Address")
    cfs = client.get_all("extras/custom-fields/")
    raw["custom_fields"] = cfs
    relevant = []
    for cf in cfs:
        objs = cf.get("object_types") or cf.get("content_types") or []
        for o in objs:
            if str(o).endswith(("prefix", "ipaddress", "iprange",
                                "service", "vlan")):
                relevant.append(cf)
                break
    if not relevant:
        miss("no custom fields exist on Prefix, IP Address, IP Range, Service, or VLAN")
        info("a custom field `kea_role` on `ipam.prefix` with choices "
             "bootstrap, crew, mgmt would let the renderer pick prefixes "
             "by role rather than by hard coded CIDR")
    else:
        for cf in relevant:
            info(f"  name={cf.get('name')!s:<22} "
                 f"type={cf.get('type',{}).get('value','?')!s:<10} "
                 f"on={cf.get('object_types') or cf.get('content_types')}")


def audit_prefix_roles(raw: dict, client: NetboxClient) -> None:
    header("3.  Prefix roles")
    roles = client.get_all("ipam/roles/")
    raw["prefix_roles"] = roles
    if not roles:
        miss("no Prefix Roles defined")
        info("a Prefix Role `kea-bootstrap` and `kea-crew` would let the "
             "renderer find the right Prefix without a hard coded CIDR")
    else:
        for r in roles:
            info(f"  slug={r.get('slug')!s:<24} name={r.get('name')}")


def audit_known_prefix(raw: dict, client: NetboxClient,
                       label: str, cidr: str) -> None:
    header(f"4.  Prefix lookup, {label}, {cidr}")
    results = client.get_all(f"ipam/prefixes/?prefix={cidr}")
    raw.setdefault("prefixes", {})[label] = results
    if not results:
        miss(f"prefix {cidr} not found in IPAM")
        return
    for p in results:
        info(f"  id={p['id']}")
        info(f"  prefix={p['prefix']}")
        info(f"  description={p.get('description')!r}")
        info(f"  vlan={(p.get('vlan') or {}).get('display','-')}")
        info(f"  role={(p.get('role') or {}).get('slug','-')}")
        info(f"  tags={[t.get('slug') for t in p.get('tags', [])]}")
        info(f"  custom_fields={p.get('custom_fields', {})}")
        info(f"  status={(p.get('status') or {}).get('value','?')}")


def audit_ip_ranges(raw: dict, client: NetboxClient) -> None:
    header("5.  IP Ranges (the right home for DHCP dynamic pools)")
    ranges = client.get_all("ipam/ip-ranges/")
    raw["ip_ranges"] = ranges
    if not ranges:
        miss("no IP Ranges defined")
        info("an IP Range like 92.33.43.198 to 92.33.43.254 inside the "
             "bootstrap Prefix is the canonical home for a DHCP pool")
        return
    info(f"{len(ranges)} IP Range(s) defined")
    for r in ranges:
        info(f"  {r.get('start_address'):>22} .. {r.get('end_address')}  "
             f"desc={r.get('description')!r}  "
             f"tags={[t.get('slug') for t in r.get('tags', [])]}")


def audit_known_service_ip(raw: dict, client: NetboxClient,
                           label: str, addr: str) -> None:
    header(f"6.  IP Address lookup, {label}, {addr}")
    results = client.get_all(f"ipam/ip-addresses/?address={addr}")
    raw.setdefault("service_ips", {})[label] = results
    if not results:
        miss(f"no IP Address {addr} found in IPAM")
        info("not necessarily a problem, only a problem if we want to "
             "source the service IP from NetBox")
        return
    for ip in results:
        ao = ip.get("assigned_object") or {}
        dev = (ao.get("device") or {}).get("name") if ao else None
        info(f"  id={ip['id']}")
        info(f"  address={ip['address']}")
        info(f"  description={ip.get('description')!r}")
        info(f"  dns_name={ip.get('dns_name')!r}")
        info(f"  role={ip.get('role') and ip['role'].get('value', '?')}")
        info(f"  tags={[t.get('slug') for t in ip.get('tags', [])]}")
        info(f"  assigned_to={dev or '<unassigned>'} ({ao.get('name', '-')})")


def audit_services(raw: dict, client: NetboxClient) -> None:
    header("7.  IPAM Services (the right home for DNS, NTP, TFTP)")
    services = client.get_all("ipam/services/")
    raw["services"] = services
    if not services:
        miss("no IPAM Services defined")
        info("a Service `dns` on the DNS hosts and `tftp` on tftp."
             "infra.glitched.se would let the renderer find them by name")
        return
    info(f"{len(services)} Service(s) defined")
    for s in services:
        name = s.get("name")
        proto = (s.get("protocol") or {}).get("value", "?")
        ports = s.get("ports")
        dev = (s.get("device") or {}).get("name")
        vm = (s.get("virtual_machine") or {}).get("name")
        info(f"  name={name!s:<10} proto={proto:<4} ports={ports} on "
             f"device={dev or '-'} vm={vm or '-'}")


def audit_service_hosts(raw: dict, client: NetboxClient) -> None:
    header("8.  Devices and VMs that look like service hosts")
    devices = client.get_all("dcim/devices/")
    vms = client.get_all("virtualization/virtual-machines/")
    raw["service_devices"] = []
    raw["service_vms"] = []
    for d in devices:
        n = (d.get("name") or "").lower()
        if any(frag in n for frag in KNOWN_SERVICE_HOSTNAME_FRAGMENTS):
            ip = (d.get("primary_ip4") or {}).get("address")
            info(f"  device {d['name']:<30} primary_ip4={ip}")
            raw["service_devices"].append(d)
    for v in vms:
        n = (v.get("name") or "").lower()
        if any(frag in n for frag in KNOWN_SERVICE_HOSTNAME_FRAGMENTS):
            ip = (v.get("primary_ip4") or {}).get("address")
            info(f"  vm     {v['name']:<30} primary_ip4={ip}")
            raw["service_vms"].append(v)
    if not raw["service_devices"] and not raw["service_vms"]:
        miss("no devices or VMs named dns*, ntp*, tftp*, ns0*, or dhcp*")


def audit_config_contexts(raw: dict, client: NetboxClient) -> None:
    header("9.  Config Contexts (a clean home for domain-name etc.)")
    contexts = client.get_all("extras/config-contexts/")
    raw["config_contexts"] = contexts
    if not contexts:
        miss("no Config Contexts defined")
        info("a Config Context applied to Site Hall D with keys "
             "domain_name, dns_servers, ntp_servers, tftp_server would "
             "be the canonical home for the fleet wide DHCP options")
        return
    info(f"{len(contexts)} Config Context(s) defined")
    for c in contexts:
        info(f"  name={c.get('name')!r}  weight={c.get('weight')}  "
             f"data_keys={list((c.get('data') or {}).keys())}")


def section_summary() -> None:
    header("10. What to do with this output")
    info("Run the script, paste the full output back. Based on what is")
    info("already populated, the next step is one of three,")
    info("")
    info("  a. Everything is there, write a small `kea_config_from_netbox()`")
    info("     helper that resolves every constant from the audit results.")
    info("  b. Tags or roles are missing but the data is present, write a")
    info("     one shot data-fill script that adds the right tags or roles")
    info("     so the renderer can find them.")
    info("  c. The data is not in NetBox at all, agree on the model first")
    info("     (Service, IPRange, custom field, or Config Context), then")
    info("     populate and rewire.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="Dump raw API data to this JSON file for downstream ingestion.",
    )
    args = parser.parse_args()

    if not require_token():
        return 1

    client = NetboxClient()
    raw: dict[str, Any] = {}

    try:
        audit_tags(raw, client)
        audit_custom_fields(raw, client)
        audit_prefix_roles(raw, client)
        for label, cidr in KNOWN_PREFIXES.items():
            audit_known_prefix(raw, client, label, cidr)
        audit_ip_ranges(raw, client)
        for label, addr in KNOWN_SERVICE_IPS.items():
            audit_known_service_ip(raw, client, label, addr)
        audit_services(raw, client)
        audit_service_hosts(raw, client)
        audit_config_contexts(raw, client)
        section_summary()
    except RuntimeError as exc:
        print(f"\nFATAL, {exc}", file=sys.stderr)
        return 2

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(raw, fh, indent=2, default=str)
        print()
        print(f"Raw API data dumped to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
