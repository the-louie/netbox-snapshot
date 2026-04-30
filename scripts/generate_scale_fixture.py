#!/usr/bin/env python3
"""Scale fixture generator (TEST-09a1/a2/a3).

Synthesises a `tests/fixtures/scale/` directory of seed JSON files
big enough to exercise pagination, retry, and the import driver
under realistic volume. Defaults: 50 sites, 500 devices, 5000
interfaces, 2000 IP addresses, 500 cables.

Usage:

    python3 scripts/generate_scale_fixture.py --out tests/fixtures/scale \\
        [--sites 50] [--devices 500] [--interfaces 5000] \\
        [--ips 2000] [--cables 500]

The output files use the same seed format the integration seeder
consumes, so a TEST-09b runner can apply them with the same
mechanism as the small fixtures.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sites", type=int, default=50)
    parser.add_argument("--devices", type=int, default=500)
    parser.add_argument("--interfaces", type=int, default=5000)
    parser.add_argument("--ips", type=int, default=2000)
    parser.add_argument("--cables", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    # TEST-09a1, sites + devices.
    sites = []
    for i in range(args.sites):
        sites.append(
            {
                "endpoint": "dcim/sites/",
                "payload": {"name": f"Site{i:03d}", "slug": f"site-{i:03d}"},
            }
        )
    (args.out / "00-sites.json").write_text(json.dumps(sites, indent=2), encoding="utf-8")

    devices = []
    for i in range(args.devices):
        site_slug = f"site-{rng.randrange(args.sites):03d}"
        devices.append(
            {
                "endpoint": "dcim/devices/",
                "payload": {
                    "name": f"dev-{i:04d}",
                    "site": {"_resolve": ["dcim.site", site_slug]},
                    "role": {"_resolve": ["dcim.devicerole", "access_switch"]},
                    "device_type": {"_resolve": ["dcim.devicetype", "ws-c2950t-24"]},
                    "status": "active",
                },
            }
        )
    (args.out / "01-devices.json").write_text(json.dumps(devices, indent=2), encoding="utf-8")

    # TEST-09a2, interfaces.
    interfaces = []
    per_device = max(1, args.interfaces // max(1, args.devices))
    for d in range(args.devices):
        for i in range(per_device):
            interfaces.append(
                {
                    "endpoint": "dcim/interfaces/",
                    "payload": {
                        "device": {"_resolve": ["dcim.device", f"dev-{d:04d}"]},
                        "name": f"Gi0/{i}",
                        "type": "1000base-t",
                    },
                }
            )
    (args.out / "02-interfaces.json").write_text(json.dumps(interfaces, indent=2), encoding="utf-8")

    # TEST-09a3, IPs + cables.
    ips = []
    for i in range(args.ips):
        ips.append(
            {
                "endpoint": "ipam/ip-addresses/",
                "payload": {"address": f"10.{(i >> 16) & 0xFF}.{(i >> 8) & 0xFF}.{i & 0xFF}/32"},
            }
        )
    (args.out / "03-ips.json").write_text(json.dumps(ips, indent=2), encoding="utf-8")

    cables = []
    for i in range(args.cables):
        a = i % args.devices
        b = (i + 1) % args.devices
        cables.append(
            {
                "endpoint": "dcim/cables/",
                "payload": {
                    "a_terminations": [
                        {
                            "object_type": "dcim.interface",
                            "object_id": {
                                "_resolve": ["dcim.interface", [f"dev-{a:04d}", "Gi0/0"]]
                            },
                        }
                    ],
                    "b_terminations": [
                        {
                            "object_type": "dcim.interface",
                            "object_id": {
                                "_resolve": ["dcim.interface", [f"dev-{b:04d}", "Gi0/0"]]
                            },
                        }
                    ],
                    "status": "connected",
                },
            }
        )
    (args.out / "04-cables.json").write_text(json.dumps(cables, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
