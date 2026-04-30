#!/usr/bin/env python3
"""Raw NetBox API dumper, belt-and-braces backup.

Dumps every list endpoint we know about, page by page, into JSON
files under `--out`. Independent of nbsnap's transforms so this
acts as a fallback when nbsnap's structured snapshot needs
investigation.

GET-only. Safe against the production source NetBox.

Usage:

    python3 scripts/raw_api_dump.py \\
        --url https://host.docker.internal:8443 \\
        --token "$NB_SOURCE_TOKEN" \\
        --no-verify-tls \\
        --out ~/nbsnap-rescue/raw/

Output layout: `<out>/<app>/<endpoint>.json` containing the full
list response with every page's `results` concatenated.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests
import urllib3

# Endpoints worth dumping. Order does not matter for the raw dump
# (no FK resolution is happening, just byte capture). Add or
# remove freely; an endpoint that 404s gets logged and skipped.
ENDPOINTS = [
    # DCIM
    "dcim/sites/",
    "dcim/locations/",
    "dcim/racks/",
    "dcim/rack-roles/",
    "dcim/manufacturers/",
    "dcim/device-types/",
    "dcim/device-roles/",
    "dcim/platforms/",
    "dcim/devices/",
    "dcim/interfaces/",
    "dcim/front-ports/",
    "dcim/rear-ports/",
    "dcim/console-ports/",
    "dcim/console-server-ports/",
    "dcim/power-ports/",
    "dcim/power-outlets/",
    "dcim/cables/",
    "dcim/virtual-chassis/",
    "dcim/inventory-items/",
    "dcim/module-types/",
    "dcim/modules/",
    # IPAM
    "ipam/vlans/",
    "ipam/vlan-groups/",
    "ipam/roles/",
    "ipam/prefixes/",
    "ipam/ip-ranges/",
    "ipam/ip-addresses/",
    "ipam/aggregates/",
    "ipam/asns/",
    "ipam/asn-ranges/",
    "ipam/route-targets/",
    "ipam/fhrp-groups/",
    "ipam/services/",
    # Wireless
    "wireless/wireless-lans/",
    "wireless/wireless-links/",
    "wireless/wireless-lan-groups/",
    # Circuits
    "circuits/circuits/",
    "circuits/providers/",
    "circuits/circuit-terminations/",
    "circuits/circuit-types/",
    # Tenancy (out of nbsnap scope but worth capturing raw)
    "tenancy/tenants/",
    "tenancy/tenant-groups/",
    "tenancy/contacts/",
    "tenancy/contact-roles/",
    "tenancy/contact-assignments/",
    # Extras
    "extras/tags/",
    "extras/custom-fields/",
    "extras/custom-field-choice-sets/",
    "extras/custom-links/",
    "extras/config-contexts/",
    "extras/config-templates/",
    # VPN
    "vpn/tunnels/",
    "vpn/tunnel-groups/",
    "vpn/tunnel-terminations/",
    "vpn/ike-proposals/",
    "vpn/ike-policies/",
    "vpn/ipsec-proposals/",
    "vpn/ipsec-policies/",
    "vpn/ipsec-profiles/",
    "vpn/l2vpns/",
    "vpn/l2vpn-terminations/",
    # Status + schema reference
    "status/",
    "plugins/",
    "extras/content-types/",
    "extras/object-types/",
]


def fetch_endpoint(
    session: requests.Session,
    base: str,
    endpoint: str,
    timeout: int,
    verify: bool,
) -> dict:
    """Walk a list endpoint following `next`; merge `results` into one dict."""

    url = f"{base.rstrip('/')}/api/{endpoint}?limit=500"
    merged: dict | None = None
    page_count = 0
    total_yielded = 0

    while url is not None:
        # Retry budget is small on purpose, we want to see the
        # failure immediately if production starts misbehaving.
        for attempt in range(3):
            try:
                resp = session.get(url, timeout=timeout, verify=verify)
                break
            except requests.exceptions.RequestException as exc:
                if attempt == 2:
                    raise
                sys.stderr.write(f"  retry on {type(exc).__name__}: {url}\n")
                time.sleep(2**attempt)
        if resp.status_code == 404:
            return {"endpoint": endpoint, "status": "404", "results": []}
        resp.raise_for_status()
        page = resp.json()
        # Status/plugins/etc. are flat dicts, not paginated.
        if "results" not in page:
            return page
        if merged is None:
            merged = {
                "endpoint": endpoint,
                "count": page.get("count"),
                "results": [],
            }
        merged["results"].extend(page["results"])
        total_yielded += len(page["results"])
        url = page.get("next")
        page_count += 1
        sys.stderr.write(f"  page {page_count}: cumulative {total_yielded}\n")

    return merged or {"endpoint": endpoint, "results": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-verify-tls", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    if args.no_verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    args.out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Token {args.token}",
            "Accept": "application/json",
        }
    )

    failures: list[tuple[str, str]] = []
    for endpoint in ENDPOINTS:
        sys.stderr.write(f"==> {endpoint}\n")
        try:
            payload = fetch_endpoint(
                session,
                args.url,
                endpoint,
                args.timeout,
                verify=not args.no_verify_tls,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append((endpoint, str(exc)))
            sys.stderr.write(f"  FAILED: {exc}\n")
            continue

        rel = endpoint.rstrip("/").replace("/", "__") + ".json"
        target = args.out / rel
        target.write_text(json.dumps(payload, indent=2, sort_keys=True))
        sys.stderr.write(f"  wrote {target}\n")

    sys.stderr.write(f"\ndone. {len(failures)} endpoint(s) failed.\n")
    for endpoint, msg in failures:
        sys.stderr.write(f"  {endpoint}: {msg}\n")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
