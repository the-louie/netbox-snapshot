#!/usr/bin/env python3
"""
Render the 2026 Kea DHCPv4 config from NetBox into the current directory.

Outputs (written to cwd unless --outdir is given),
    kea-dhcp4.conf    the single main file, no per dist include files

The access switches no longer DHCP for a management IP, their addressing
is static and served by netbox2cisco.py over TFTP. Kea's only access
switch job is to hand each switch its own boot file at the VLAN 1 stage,
which it does through global host reservations keyed on the Option 82
circuit id, see `collect_boot_assignments`.

Every dynamic value the script emits is sourced from NetBox, no
fallback constants. Concretely,

  bootstrap subnet, router, pool     Prefix Role `kea-bootstrap` + IP Range
  crew subnet, router, pool          Prefix Role `kea-crew` + IP Range
  participant subnets                dist `irb.<vid>` networks plus the
                                     matching `kea-participant` IP Range
  DNS servers (Option 6)             IPAddresses with dns_name like `ns0*`
                                     restricted to family 4
  TFTP server (next-server)          IPAddress with dns_name `tftp.<domain>`
  NTP servers (Option 42)            IPAddresses with dns_name like `ntp*`
                                     restricted to family 4

If any of those lookups returns empty the script fails rather than
emitting a config with hidden defaults.

For each access switch (role `access_switch`), one global host reservation,
  - circuit-id      = hex of the dist port's `description`
  - boot-file-name  = <name>.conf lowercased, see access_config_filename
  - next-server     = the TFTP server address

The dist port description is opaque to the renderer, it is hex encoded
as the Kea circuit id and matched against whatever the Junos relay agent
emits at runtime. The operator sets the description on the dist port in
NetBox, the renderer copies whatever it finds and Kea matches it. The
boot-file-name is the same string netbox2cisco.py writes the rendered
file as, both call `access_config_filename` so they cannot drift.

The reservations are global (no ip-address), so Kea consults them no
matter which participant or bootstrap subnet the phase 1 packet lands in.

Usage,
    export NB_TOKEN="..."
    ./netbox2kea.py
    ./netbox2kea.py --outdir ./out
    ./netbox2kea.py --overwrite
"""

from __future__ import annotations

import argparse
import glob
import ipaddress
import json
import logging
import os
import sys

from netbox_utils.netbox_common import (
    INFRASTRUCTURE_VIDS,
    NETBOX_HOST,
    NetboxClient,
    ROLE_ACCESS,
    ROLE_DIST,
    access_config_filename,
    access_uplinks,
    atomic_write_text,
    configure_logging,
    confirm_overwrite,
    kea_service_ip_exact,
    kea_service_ips,
    kea_subnet_from_role,
    require_token,
    strip_prefix_len,
)

# Module logger, named after the script so a multi script run can be
# filtered by source. The actual handler and level are wired up by
# `configure_logging` in main, this just gives the module a stable name.
log = logging.getLogger("netbox2kea")

# The domain name does not have a dedicated NetBox field, see the
# audit, so it stays a constant. Promote to a Site custom field or a
# Config Context later if the deployment needs more than one domain.
DOMAIN_NAME = "infra.glitched.se"


# ---------------------------------------------------------------------------
# NetBox to in memory reservations
# ---------------------------------------------------------------------------

def collect_boot_assignments(client: NetboxClient,
                             tftp_server: str) -> list[dict]:
    """
    Walk every access switch and turn its uplink dist port description
    into a global Kea host reservation that carries only the circuit id,
    the per switch boot file, and the TFTP next-server. Returns the
    reservations sorted by circuit id for byte stable output.

    The boot file is assigned at the VLAN 1 / phase 1 stage. The dist
    injects Option 82 with the port description as the circuit id on that
    relay, so Kea can hand each switch its own config filename before the
    switch holds any management address. Management addressing is then
    fully static from the downloaded file, there is no VLAN 600 DHCP
    reservation, so the reservation carries no ip-address and no hostname.

    The reservations are global, a reservation with no ip-address is
    consulted regardless of which participant or bootstrap subnet the
    phase 1 packet matched, which decouples the boot assignment from the
    participant subnet mapping.
    """
    cables = client.get_all("dcim/cables/")
    access_devices = client.get_all(f"dcim/devices/?role={ROLE_ACCESS}")
    access_by_id = {d["id"]: d for d in access_devices}
    uplinks = access_uplinks(cables, access_by_id)

    reservations: list[dict] = []
    skipped = 0

    for dev_id, edges in uplinks.items():
        access_dev = access_by_id[dev_id]
        hostname = access_dev["name"]
        boot_file = access_config_filename(access_dev)

        # Emit one reservation per uplink rather than only the first.
        # The Kea reservation matches a specific circuit id, so a
        # packet that traverses the second uplink carries the second
        # dist port's circuit id and the previous "first edge wins"
        # would silently miss it. Same access switch, same boot file,
        # multiple circuit ids.
        emitted_for_switch = 0
        for dist_obj, _dist_dev in edges:
            port_desc = (dist_obj.get("description") or "").strip()
            if not port_desc:
                print(f"  [warn] {hostname}, dist port "
                      f"{dist_obj.get('name')!r} has no description, "
                      f"skipping this uplink", file=sys.stderr)
                continue

            # Port descriptions are ASCII by construction (the cable
            # script emits hostnames and slot letters). The encode
            # catches a stray non ASCII character here rather than at
            # config validation time.
            try:
                circuit_id_hex = port_desc.encode("ascii").hex()
            except UnicodeEncodeError:
                print(f"  [warn] {hostname}, port description "
                      f"{port_desc!r} contains non ASCII characters, "
                      f"skipping this uplink", file=sys.stderr)
                continue

            reservations.append({
                "circuit-id":     circuit_id_hex,
                "boot-file-name": boot_file,
                "next-server":    tftp_server,
            })
            emitted_for_switch += 1

        if emitted_for_switch == 0:
            # Every uplink was unusable (missing or non ASCII
            # description), the switch ends up with no reservation at
            # all, which is the same outcome the previous single edge
            # logic would have produced.
            skipped += 1

    # Access switches with no uplink cable never appear in `uplinks`,
    # surface the count so a missing cable is visible rather than silently
    # leaving a switch with no boot file.
    no_uplink = sorted(access_by_id[i]["name"]
                       for i in access_by_id if i not in uplinks)
    if no_uplink:
        print(f"  [warn] {len(no_uplink)} access switch(es) have no uplink "
              f"cable, no boot reservation emitted, {no_uplink}",
              file=sys.stderr)

    reservations.sort(key=lambda r: (r["circuit-id"], r["boot-file-name"]))
    if skipped:
        print(f"  [info] {skipped} access switch(es) skipped (every "
              f"uplink had a missing or non ASCII dist port description)",
              file=sys.stderr)
    return reservations


def collect_participant_subnets(client: NetboxClient,
                                special_subnets: set[str]) -> list[dict]:
    """
    Build one Kea DHCPv4 subnet per participant VLAN, sourced from the dist
    `irb.<vid>` interfaces. VLAN 1 is the default participant VLAN, a device
    that matches Option 60/61 `cisco` is steered to TFTP by the global
    `cisco_option61` class, every other device gets a participant lease from
    the pool defined here, so each participant VLAN needs its own subnet.

    The subnet CIDR and the router both come from the IRB address itself,
    which is authoritative for the L3 first hop, the same address the Junos
    relay carries as giaddr, so Kea always selects the right subnet and the
    router is guaranteed to sit inside it. The pool comes from a NetBox IP
    Range with role `kea-participant` that starts inside the subnet.

    `special_subnets` holds the bootstrap and crew subnet strings, those two
    participant VLANs are emitted through their own roles, so they are
    skipped here to avoid Kea seeing a duplicate subnet definition. A
    participant IRB with no matching `kea-participant` range is warned about
    and skipped rather than failing the whole run, one unprovisioned table
    should not block the fleet.

    Subnet id is the VLAN id, which cannot collide with the dist mgmt ids
    (third mgmt octet times ten, at most 990 but in practice well under the
    participant range) or the bootstrap and crew ids, the caller asserts
    this. Returns subnets sorted by id for stable output.
    """
    part_ranges = client.get_all("ipam/ip-ranges/?role=kea-participant")

    def pool_for(net: ipaddress.IPv4Network) -> str | None:
        """
        Resolve the `<start> - <end>` pool string for a participant /24
        from a kea-participant IP Range that sits inside it. Returns
        None when no Range starts in the /24, raises RuntimeError on
        operator data the caller cannot recover from,

          * two or more Ranges start inside the same /24, the previous
            "first match wins" was non deterministic because NetBox
            pagination order is not guaranteed (matches the
            kea-dist-mgmt invariant from kea_dist_pool_for_subnet)
          * the Range's end falls outside the /24 (the pool would
            spill the routed subnet)
          * start >= end (an inverted or zero length Range)
          * the Range contains `network_address + 1` (the IRB
            gateway, Kea would hand out the routers option as a
            participant lease)
        """
        candidates: list[dict] = []
        for r in part_ranges:
            start = strip_prefix_len(r.get("start_address"))
            if start and ipaddress.ip_address(start) in net:
                candidates.append(r)
        if not candidates:
            return None
        if len(candidates) > 1:
            labels = [c.get("display")
                      or f"{strip_prefix_len(c['start_address'])}-"
                         f"{strip_prefix_len(c['end_address'])}"
                      for c in candidates]
            raise RuntimeError(
                f"{len(candidates)} kea-participant IP Ranges start "
                f"inside {net}, expected exactly one, {labels}"
            )
        r = candidates[0]
        start = strip_prefix_len(r["start_address"])
        end = strip_prefix_len(r.get("end_address"))
        start_ip = ipaddress.ip_address(start)
        end_ip = ipaddress.ip_address(end) if end else None
        label = r.get("display") or f"{start}-{end}"
        if end_ip is None or end_ip not in net:
            raise RuntimeError(
                f"kea-participant IP Range {label!r} starts inside "
                f"{net} but ends at {end!r}, which is outside the "
                f"subnet"
            )
        if start_ip >= end_ip:
            raise RuntimeError(
                f"kea-participant IP Range {label!r} has start "
                f"{start} >= end {end}, fix the bounds in NetBox"
            )
        gateway = net.network_address + 1
        if start_ip <= gateway <= end_ip:
            raise RuntimeError(
                f"kea-participant IP Range {label!r} covers the "
                f"IRB gateway {gateway} inside {net}, Kea would "
                f"lease the routers option to a participant"
            )
        return f"{start} - {end}"

    subnets: list[dict] = []
    seen: dict[int, str] = {}
    for device in client.get_all(f"dcim/devices/?role={ROLE_DIST}"):
        dev_id = device["id"]
        names = {i["id"]: i["name"]
                 for i in client.get_all(
                     f"dcim/interfaces/?device_id={dev_id}")}
        for ip in client.get_all(f"ipam/ip-addresses/?device_id={dev_id}"):
            nm = names.get((ip.get("assigned_object") or {}).get("id"), "")
            if not nm.startswith("irb."):
                continue
            try:
                vid = int(nm.split(".")[1])
            except (IndexError, ValueError):
                continue
            if vid in INFRASTRUCTURE_VIDS:
                continue
            ifc = ipaddress.ip_interface(ip["address"])
            if ifc.version != 4:
                continue
            cidr = str(ifc.network)
            if cidr in special_subnets:
                continue
            if vid in seen and seen[vid] != cidr:
                raise RuntimeError(
                    f"VLAN {vid} maps to two participant subnets, "
                    f"{seen[vid]} and {cidr}, fix the dist irb.{vid} "
                    f"addressing in NetBox"
                )
            if vid in seen:
                continue
            pool = pool_for(ifc.network)
            if pool is None:
                print(f"  [warn] participant VLAN {vid} ({cidr}) on "
                      f"{device['name']} has no kea-participant IP Range, "
                      f"skipping", file=sys.stderr)
                continue
            seen[vid] = cidr
            subnets.append({
                "id": vid,
                "subnet": cidr,
                "pools": [{"pool": pool}],
                "option-data": [{"name": "routers", "data": str(ifc.ip)}],
                "reservations": [],
            })
    return sorted(subnets, key=lambda s: s["id"])


def render_main(bootstrap: dict[str, str],
                crew: dict[str, str],
                dns_servers: list[str],
                tftp_server: str,
                ntp_servers: list[str],
                participants: list[dict],
                reservations: list[dict]) -> str:
    """
    Build the main kea-dhcp4.conf content. The dynamic inputs (the two
    Kea subnet definitions from the bootstrap and crew roles, the DNS,
    TFTP, NTP addresses, the participant subnet list, and the global boot
    reservation list) come from the caller, the static structure stays in
    the function body. `participants` is a ready to serialise list of Kea
    subnet dicts, one per participant VLAN, from
    `collect_participant_subnets`. `reservations` is the global host
    reservation list from `collect_boot_assignments`.

    There are no per dist mgmt subnets, the access switches do not DHCP on
    VLAN 600 any more, their management addressing is static and served by
    netbox2cisco.py, so Kea has no reason to define the 172.16.<n>.0/24
    mgmt subnets or hold reservations inside them.
    """
    subnets: list[dict] = []

    subnets.append({
        "id": 1,
        "subnet": bootstrap["subnet"],
        "pools":  [{"pool": bootstrap["pool"]}],
        "option-data": [{"name": "routers", "data": bootstrap["router"]}],
        "reservations": [],
    })

    # Participant subnets, one per dist participant IRB. These carry no
    # reservations, the Cisco access switches that share the VLAN are
    # steered to TFTP by the global cisco_option61 class, everything else
    # draws a lease from the pool.
    subnets.extend(participants)

    subnets.append({
        "id": 99,
        "subnet": crew["subnet"],
        "pools":  [{"pool": crew["pool"]}],
        "option-data": [{"name": "routers", "data": crew["router"]}],
        "reservations": [],
    })

    # The Kea Option 6 csv-format accepts both spaced and unspaced
    # separators, matching the 2025 config files keeps diffs small for
    # operators comparing rendered output against the previous year.
    dns_data = ",".join(dns_servers)
    ntp_data = ",".join(ntp_servers)

    config = {
        "Dhcp4": {
            "valid-lifetime": 3600,
            "renew-timer":    900,
            "rebind-timer":   1800,
            "host-reservation-identifiers": ["circuit-id"],
            # Boot file assignments are global, see collect_boot_assignments,
            # so Kea consults them no matter which participant or bootstrap
            # subnet the phase 1 packet matched. The reservations carry no
            # ip-address, the address still comes from the matched subnet's
            # pool, so the usual caution against global address reservations
            # does not apply.
            "reservations-global":   True,
            "reservations-in-subnet": True,
            "reservations": reservations,
            "multi-threading": {
                "enable-multi-threading": True,
                "thread-pool-size": 4,
                "packet-queue-size": 64,
            },
            "interfaces-config": {"interfaces": ["*"]},
            "lease-database":    {"type": "memfile", "lfc-interval": 3600},
            "option-data": [
                {"name": "domain-name-servers", "code": 6,  "space": "dhcp4",
                 "csv-format": True, "data": dns_data},
                {"name": "domain-name",          "code": 15, "space": "dhcp4",
                 "csv-format": True, "data": DOMAIN_NAME},
                {"name": "ntp-servers",          "code": 42, "space": "dhcp4",
                 "csv-format": True, "data": ntp_data},
            ],
            "client-classes": [
                # Match Cisco access switches at the VLAN 1 stage. The
                # boot file name and the TFTP next-server are supplied
                # by the per switch global host reservations
                # (see `collect_boot_assignments`), not at class level,
                # so the reservation is the single source of truth and
                # no operator edit can drift the two definitions apart.
                # A switch with no matching reservation simply gets no
                # boot file from these classes, which is intentional,
                # an unprovisioned switch must not silently fetch a
                # generic config.
                {"name": "cisco_option61",
                 "test": "substring(option[61].hex,1,5) == 'cisco'"},
                {"name": "cisco_pnp",
                 "test": "member('VENDOR_CLASS_ciscopnp')"},
            ],
            "subnet4": subnets,
            "control-socket": {
                "socket-type": "unix",
                "socket-name": "/var/run/kea/kea4-ctrl-socket",
            },
            "loggers": [{
                "name": "kea-dhcp4",
                "output_options": [{
                    "output":  "/var/log/kea/kea-dhcp4.log",
                    "maxsize": 2048000,
                    "maxver":  4,
                }],
                "severity":   "INFO",
                "debuglevel": 55,
            }],
        }
    }

    # The config is a single self contained file now, no per dist include
    # directives, so the JSON serialises straight through with no marker
    # substitution.
    text = json.dumps(config, indent=4)

    header = (
        "// Generated by netbox2kea.py, do not hand edit.\n"
        f"// Source of truth, NetBox at {NETBOX_HOST}\n"
        "// Per switch boot files are assigned by the global reservations,\n"
        "// the static configs themselves are rendered by netbox2cisco.py.\n"
        "\n"
    )
    return header + text + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outdir",
        default=".",
        help="Directory to write the file into. Default cwd.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing files in --outdir without prompting. The "
             "default behaviour prompts on a TTY and refuses on a non "
             "interactive run.",
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

    os.makedirs(args.outdir, exist_ok=True)

    client = NetboxClient()

    # Resolve every constant the renderer needs from NetBox up front. A
    # missing Role or IP is surfaced here rather than during file write so
    # the operator sees the cause immediately.
    print("Resolving Kea constants from NetBox ...")
    try:
        bootstrap = kea_subnet_from_role(client, "kea-bootstrap")
        crew = kea_subnet_from_role(client, "kea-crew")
    except RuntimeError as exc:
        print(f"Error, {exc}", file=sys.stderr)
        return 2
    # DHCPv4 Option 6 accepts IPv4 only, the family filter keeps any v6
    # ns0* records from leaking into the dhcp4 option-data block. The
    # NTP servers follow the same prefix match so the operator can list
    # one or many.
    dns_servers = kea_service_ips(client, "ns0", family=4)
    tftp_server = kea_service_ip_exact(client, f"tftp.{DOMAIN_NAME}")
    ntp_servers = kea_service_ips(client, "ntp", family=4)
    if not dns_servers:
        print("Error, no IPAddress with dns_name starting ns0 found",
              file=sys.stderr)
        return 2
    if not tftp_server:
        print(f"Error, no IPAddress with dns_name tftp.{DOMAIN_NAME} found",
              file=sys.stderr)
        return 2
    if not ntp_servers:
        print("Error, no IPAddress with dns_name starting ntp found",
              file=sys.stderr)
        return 2
    print(f"  bootstrap, {bootstrap['subnet']}, router {bootstrap['router']}, pool {bootstrap['pool']}")
    print(f"  crew,      {crew['subnet']}, router {crew['router']}, pool {crew['pool']}")
    print(f"  dns,       {dns_servers}")
    print(f"  tftp,      {tftp_server}")
    print(f"  ntp,       {ntp_servers}")

    print()
    print("Collecting participant subnets from NetBox ...")
    special_subnets = {bootstrap["subnet"], crew["subnet"]}
    participants = collect_participant_subnets(client, special_subnets)
    # Participant subnet ids are VLAN ids, guard against an overlap with the
    # bootstrap (id 1) or crew (id 99) subnet id before Kea would reject the
    # duplicate, so the operator sees the offending VLAN here. There are no
    # per dist mgmt subnet ids any more, so those two are the only reserved
    # ids.
    reserved_ids = {1, 99}
    clashes = {p["id"] for p in participants} & reserved_ids
    if clashes:
        print(f"Error, participant VLAN id(s) {sorted(clashes)} collide with "
              f"the bootstrap or crew subnet id", file=sys.stderr)
        return 2
    print(f"  {len(participants)} participant subnets")

    print()
    print("Collecting boot file assignments from NetBox ...")
    reservations = collect_boot_assignments(client, tftp_server)
    print(f"  {len(reservations)} access switch boot reservations")

    # Only the single main file is written now, the overwrite guard
    # compares it against any existing file before the first byte changes.
    main_path = os.path.join(args.outdir, "kea-dhcp4.conf")
    if not confirm_overwrite([main_path], args.overwrite):
        return 1

    # Stale per dist include files from the pre static config model
    # (kea-dhcp4-access-<slug>.conf) are no longer emitted but may
    # still sit in the outdir from a previous run. Kea would happily
    # load them again if an operator ever re added an <?include?>
    # directive, so an outdir with both the new single file and the
    # old per dist files is operator drift that should be surfaced.
    # Without --overwrite the renderer refuses, naming the leftovers,
    # with --overwrite the renderer deletes them.
    stale_pattern = os.path.join(args.outdir, "kea-dhcp4-access-*.conf")
    stale_files = sorted(glob.glob(stale_pattern))
    if stale_files:
        if args.overwrite:
            print(f"Note, --overwrite is set, deleting {len(stale_files)} "
                  f"stale per dist file(s) from a previous renderer model.",
                  file=sys.stderr)
            for sf in stale_files:
                os.remove(sf)
        else:
            print(f"Error, {len(stale_files)} stale per dist file(s) from "
                  f"a previous renderer model are present in {args.outdir}, "
                  f"the current model emits a single self contained "
                  f"kea-dhcp4.conf and Kea would load both if an "
                  f"<?include?> directive were ever restored. Rerun with "
                  f"--overwrite to delete them.",
                  file=sys.stderr)
            for sf in stale_files[:3]:
                print(f"  {sf}", file=sys.stderr)
            if len(stale_files) > 3:
                print(f"  ... and {len(stale_files) - 3} more",
                      file=sys.stderr)
            return 1

    print()
    print(f"Writing {main_path} ...")
    atomic_write_text(main_path, render_main(
        bootstrap, crew, dns_servers, tftp_server,
        ntp_servers, participants, reservations))
    print(f"  wrote {main_path}")

    print()
    print("=" * 60)
    print(f"  Boot reservations,   {len(reservations)}")
    print(f"  Participant subnets, {len(participants)}")
    print(f"  Main file,           {main_path}")
    print("=" * 60)

    # The operator guidance block exists for interactive runs, in a CI
    # pipeline the block adds noise to the run log without changing
    # the behaviour. Skipping on a non TTY stdout is the conservative
    # default, the summary above stays in every run.
    if sys.stdout.isatty():
        print()
        print("Next,")
        print("  1. Render the per switch configs, ./netbox2cisco.py")
        print("  2. Copy kea-dhcp4.conf to /etc/kea/ and the access configs "
              "to the TFTP root")
        print("  3. kea-dhcp4 -t /etc/kea/kea-dhcp4.conf")
        print("  4. salt 'dhcp*.infra.glitched.se' state.apply kea_2026")

    return 0


if __name__ == "__main__":
    sys.exit(main())
