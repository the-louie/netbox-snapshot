"""End-to-end test for `nbsnap reset-destination`.

Skipped when the netbox-docker dest stack at localhost:8081 is
down. To run locally:

    make stack-up stack-wait stack-seed
    pytest tests/integration/test_reset_destination.py -v

The seed step is important: without it the test asserts on an
empty destination both before and after the reset, which would
pass trivially. The seed gives us a non-zero "before" count.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import requests

from nbsnap.reset_cli import EXIT_OK, run_reset_cli

from .conftest import DEST_TOKEN, DEST_URL


def _args(**override) -> argparse.Namespace:
    defaults = {
        "url": DEST_URL,
        "token": DEST_TOKEN,
        "no_verify_tls": True,
        "content_types": None,
        "keep": [],
        "apply": True,
        "confirmed": True,
        "on_error": "continue",
        "audit_out": None,
    }
    defaults.update(override)
    return argparse.Namespace(**defaults)


def _site_count() -> int:
    """One small read against the destination, used as the
    before/after sanity check."""

    resp = requests.get(
        f"{DEST_URL}/api/dcim/sites/",
        headers={"Authorization": f"Token {DEST_TOKEN}"},
        timeout=10,
    )
    resp.raise_for_status()
    return int(resp.json()["count"])


@pytest.mark.usefixtures("require_stack")
def test_reset_clears_seeded_destination(tmp_path: Path) -> None:
    """After running the reset against a seeded dest, the in-scope
    endpoints return zero rows."""

    pre = _site_count()
    if pre == 0:
        pytest.skip("destination has no sites to delete; run `make stack-seed` first")

    audit_path = tmp_path / "audit.jsonl"
    rc = run_reset_cli(_args(audit_out=audit_path))
    assert rc == EXIT_OK, "reset returned a non-zero exit code"

    post = _site_count()
    assert post == 0, f"sites remain on the destination after reset (count={post})"

    # Audit JSONL exists and lists at least one deleted row.
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert rows, "audit file is empty"
    site_rows = [r for r in rows if r["content_type"] == "dcim.site"]
    assert site_rows
    # Every site row should have an outcome that indicates a delete.
    assert all(r["outcome"] in {"deleted", "deleted-fallback"} for r in site_rows)


@pytest.mark.usefixtures("require_stack")
def test_reset_keep_flag_preserves_named_record() -> None:
    """`--keep <slug>` survives the wipe. We re-seed first via
    the existing fixture and then assert the kept site is still
    there."""

    pre = _site_count()
    if pre == 0:
        pytest.skip("destination has no sites; run `make stack-seed` first")

    # The seed includes a site with slug `hall-d`. Keep it.
    rc = run_reset_cli(_args(keep=["hall-d"]))
    assert rc == EXIT_OK

    resp = requests.get(
        f"{DEST_URL}/api/dcim/sites/?slug=hall-d",
        headers={"Authorization": f"Token {DEST_TOKEN}"},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    assert payload["count"] == 1, "kept site was deleted unexpectedly"
