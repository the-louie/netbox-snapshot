"""TEST-07: end-to-end cycle resolution.

Seed source with a Device + Interface + IPAddress chain whose
primary_ip4 references the IP on the device's interface.
Export + import; GET the device on the destination and assert
`primary_ip4.address` matches the source value.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from tests.integration.conftest import (
    DEST_TOKEN,
    DEST_URL,
    SOURCE_TOKEN,
    SOURCE_URL,
)


@pytest.mark.usefixtures("require_stack")
def test_primary_ip4_cycle_round_trips(tmp_path: Path) -> None:
    # Export the seeded source. We assume the test stack already
    # carries at least one device with a primary_ip4 reference;
    # the docker-compose fixture is responsible for seeding this.
    snap = tmp_path / "snap"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "nbsnap",
            "export",
            "--url",
            SOURCE_URL,
            "--token",
            SOURCE_TOKEN,
            "--out",
            str(snap),
        ],
        check=True,
    )

    # Find the first device on the source whose primary_ip4 is
    # populated. We compare its address to the destination later.
    source_devices = requests.get(
        f"{SOURCE_URL}/api/dcim/devices/",
        headers={"Authorization": f"Token {SOURCE_TOKEN}"},
        params={"limit": 0},
        timeout=10,
    ).json()["results"]
    target_device = next(
        (d for d in source_devices if d.get("primary_ip4")),
        None,
    )
    if target_device is None:
        pytest.skip("source stack has no device with primary_ip4")

    expected_address = target_device["primary_ip4"]["address"]
    device_name = target_device["name"]

    # Import to the destination.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "nbsnap",
            "import",
            "--url",
            DEST_URL,
            "--token",
            DEST_TOKEN,
            "--in",
            str(snap),
            "--on-error",
            "continue",
        ],
        check=True,
    )

    # GET the device on the destination and confirm primary_ip4
    # landed via Phase-2.
    dest_devices = requests.get(
        f"{DEST_URL}/api/dcim/devices/",
        headers={"Authorization": f"Token {DEST_TOKEN}"},
        params={"name": device_name, "limit": 0},
        timeout=10,
    ).json()["results"]
    assert dest_devices, f"device {device_name} did not land on destination"
    dest_primary = dest_devices[0].get("primary_ip4")
    assert dest_primary is not None, f"primary_ip4 was not set after Phase-2 on {device_name}"
    assert dest_primary["address"] == expected_address, (
        f"primary_ip4 drift: source={expected_address} destination={dest_primary['address']}"
    )
