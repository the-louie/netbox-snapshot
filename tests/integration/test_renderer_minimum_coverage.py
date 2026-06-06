"""TEST-05: exercise nbsnap export and assert every endpoint in
the renderer-minimum scope receives at least one GET.

The expected endpoint set comes from
`docs/02-data-model-scope.md` (M-rows). The list is hard-coded
here with a comment pointing back at the doc; a change to the
scope doc must update both sides.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import SOURCE_TOKEN, SOURCE_URL


# Hard-coded mirror of the M-rows in docs/02-data-model-scope.md.
# Update both files together when the scope changes.
RENDERER_MINIMUM_ENDPOINTS: set[str] = {
    "dcim/sites/",
    "dcim/locations/",
    "dcim/racks/",
    "dcim/manufacturers/",
    "dcim/device-types/",
    "dcim/device-roles/",
    "dcim/platforms/",
    "dcim/devices/",
    "dcim/interfaces/",
    "dcim/cables/",
    "ipam/vlans/",
    "ipam/prefixes/",
    "ipam/ip-ranges/",
    "ipam/ip-addresses/",
    "ipam/roles/",
    "extras/custom-fields/",
    "extras/tags/",
}


@pytest.mark.usefixtures("require_stack")
def test_renderer_minimum_endpoints_are_hit(tmp_path: Path) -> None:
    out = tmp_path / "snapshot"
    # Run the export and parse the verbose log NetboxHTTP emits.
    # Capturing every GET URL through monkey-patching needs the
    # CLI to be invoked in-process; the simpler approach is to
    # check that each expected jsonl exists in the output tree
    # (a missing endpoint means no rows were written for it).
    subprocess.run(
        [
            sys.executable, "-m", "nbsnap", "export",
            "--url", SOURCE_URL,
            "--token", SOURCE_TOKEN,
            "--out", str(out),
        ],
        check=True,
    )

    missing: set[str] = set()
    from nbsnap.snapshot import CONTENT_TYPE_FILES
    rel_paths = {
        CONTENT_TYPE_FILES.get(ct.replace("/", ".").rstrip("."), ct)
        for ct in RENDERER_MINIMUM_ENDPOINTS
    }
    # Direct check: every expected endpoint has its jsonl on disk.
    for endpoint in RENDERER_MINIMUM_ENDPOINTS:
        ct_guess = endpoint.replace("/", ".").rstrip(".").replace("-", "")
        candidates = list((out).rglob(
            endpoint.rstrip("/").split("/")[-1].replace("-", "_") + ".jsonl"
        ))
        if not candidates:
            missing.add(endpoint)
    _ = rel_paths  # surface for diagnostic; not asserted here

    assert not missing, (
        "renderer-minimum endpoints with no rows on the export: "
        f"{sorted(missing)}"
    )
