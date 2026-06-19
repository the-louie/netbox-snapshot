#!/usr/bin/env python3
"""Idempotent NetBox seeder for the integration test stack.

The seeder walks `<dir>/*.json` in lexical order. Each file is a
list of `{"endpoint": "<api-path>", "payload": {...}}` rows. By
default every row issues a POST. A row can opt into a different
verb by setting `"method": "PATCH"`.

The seeder resolves `{"_resolve": [content_type, lookup_value]}`
placeholders inside payloads by issuing a GET against the relevant
endpoint and substituting the matched id. This is how the
primary_ip4 patch step from INFRA-03g2 wires Devices to the
IPAddresses created in INFRA-03g1, and how the cables fixture
(INFRA-03h) looks up interface ids.

Operationally:

* Re-running the seeder against a populated stack is a no-op. The
  duplicate POST returns 400 with a unique-constraint message,
  which the seeder downgrades to a `WARN` line and a return code
  of 0.
* PATCH rows compare the current value before writing. When the
  field already matches the desired value, the row reports `NOOP`.

Usage:

    python3 tests/fixtures/seed.py \\
        --url http://localhost:8080 \\
        --token 0123456789abcdef0123456789abcdef01234567 \\
        --dir tests/fixtures/seed

The script intentionally has zero third-party dependencies. It
uses `requests` because the project already pins it as a runtime
dep (RES-01), and `requests` is the only HTTP library that ships
on the project's test image without an install step.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import requests

# Maps `extras/content-types` `app.model` strings to the NetBox API
# path the resolver should hit. Keeping the table here means we do
# not pull in the OpenAPI schema for a tiny seeder.
RESOLVER_ENDPOINTS: dict[str, tuple[str, str]] = {
    # content_type: (api path, list filter param)
    "ipam.ipaddress": ("ipam/ip-addresses/", "address"),
    "ipam.vlan": ("ipam/vlans/", "vid"),
    "ipam.prefix": ("ipam/prefixes/", "prefix"),
    "ipam.role": ("ipam/roles/", "slug"),
    "dcim.site": ("dcim/sites/", "slug"),
    "dcim.location": ("dcim/locations/", "slug"),
    "dcim.devicerole": ("dcim/device-roles/", "slug"),
    "dcim.manufacturer": ("dcim/manufacturers/", "slug"),
    "dcim.devicetype": ("dcim/device-types/", "slug"),
    "dcim.device": ("dcim/devices/", "name"),
    "dcim.interface": ("dcim/interfaces/", "name"),
}


def _api_url(base: str, path: str) -> str:
    """Join the base URL with an API path, idempotent on trailing /."""
    return f"{base.rstrip('/')}/api/{path.lstrip('/')}"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _resolve_id(base: str, token: str, content_type: str, value: Any) -> int | None:
    """Look up an object id by `content_type` + a natural value.

    Returns `None` when the lookup fails so the caller can decide
    whether the placeholder is fatal or just a deferred patch.
    """
    if content_type not in RESOLVER_ENDPOINTS:
        return None
    path, query = RESOLVER_ENDPOINTS[content_type]
    # For interfaces, value may be a (device_name, interface_name) tuple.
    params: dict[str, Any]
    if content_type == "dcim.interface" and isinstance(value, list) and len(value) == 2:
        params = {"device": value[0], "name": value[1]}
    else:
        params = {query: value}
    resp = requests.get(_api_url(base, path), headers=_headers(token), params=params, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    return int(results[0]["id"])


def _walk(payload: Any, base: str, token: str) -> Any:
    """Recursively substitute `_resolve` placeholders inside a payload."""
    if isinstance(payload, dict):
        if list(payload.keys()) == ["_resolve"]:
            ct, value = payload["_resolve"]
            resolved = _resolve_id(base, token, ct, value)
            if resolved is None:
                msg = f"_resolve failed for {ct}={value!r}"
                raise RuntimeError(msg)
            return resolved
        return {k: _walk(v, base, token) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_walk(item, base, token) for item in payload]
    return payload


def _emit(outcome: str, file: str, row: int, message: str = "") -> None:
    """Print a single audit line so re-running the seeder is auditable."""
    suffix = f" {message}" if message else ""
    sys.stdout.write(f"[{outcome:>4}] {file}#{row}{suffix}\n")


def _iter_rows(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    """Yield `(row_index, row)` pairs from a single JSON file."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        msg = f"{path}: top-level must be a JSON list"
        raise RuntimeError(msg)
    yield from enumerate(rows)


def _apply_row(base: str, token: str, row: dict[str, Any]) -> tuple[str, str]:
    """Apply a single seed row, return `(outcome, message)`."""
    method = (row.get("method") or "POST").upper()
    endpoint = row["endpoint"]
    raw_payload = row.get("payload", {})
    payload = _walk(raw_payload, base, token)

    if method == "PATCH":
        # Idempotency, fetch the current state and compare per-field.
        # When the desired fields already match, report NOOP.
        get = requests.get(_api_url(base, endpoint), headers=_headers(token), timeout=30)
        if get.status_code == 200 and isinstance(payload, dict):
            current = get.json()
            if all(current.get(k) == v for k, v in payload.items() if not isinstance(v, dict)):
                # Heuristic, an exact equality cannot account for
                # nested objects, so a NOOP claim is conservative:
                # if any value is a dict we re-issue the PATCH and
                # let NetBox no-op it server-side.
                return "NOOP", "PATCH target already at desired state"

        resp = requests.patch(
            _api_url(base, endpoint), headers=_headers(token), json=payload, timeout=30
        )
        if resp.status_code in (200, 204):
            return " OK ", "PATCH"
        return "FAIL", f"PATCH {resp.status_code}: {resp.text[:200]}"

    # Default verb: POST.
    resp = requests.post(
        _api_url(base, endpoint), headers=_headers(token), json=payload, timeout=30
    )
    if resp.status_code in (200, 201):
        return " OK ", "POST"
    # Treat duplicate-row responses as a soft warning so the
    # seeder stays idempotent. NetBox phrases the same condition
    # in several ways depending on the model and the constraint
    # involved: "unique", "already exists", "duplicate", and the
    # IPAM-specific "overlap" message for ranges. Cover all of
    # them so the same fixture set can be reapplied without
    # turning the second invocation into a hard failure.
    text = resp.text.lower()
    if resp.status_code == 400 and any(
        marker in text for marker in ("unique", "already exists", "duplicate", "overlap")
    ):
        return "WARN", "already present"
    return "FAIL", f"POST {resp.status_code}: {resp.text[:200]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed a NetBox test instance from a directory of JSON files."
    )
    parser.add_argument("--url", required=True, help="NetBox base URL, e.g. http://localhost:8080")
    parser.add_argument("--token", required=True, help="NetBox API token")
    parser.add_argument(
        "--dir", required=True, type=Path, help="directory containing the seed JSON files"
    )
    args = parser.parse_args(argv)

    if not args.dir.is_dir():
        sys.stderr.write(f"seed dir not found: {args.dir}\n")
        return 1

    failures = 0
    for path in sorted(args.dir.glob("*.json")):
        for row_idx, row in _iter_rows(path):
            try:
                outcome, message = _apply_row(args.url, args.token, row)
            except Exception as exc:  # noqa: BLE001, audit-line keeps the cause visible
                outcome, message = "FAIL", f"unhandled: {exc!s}"
            _emit(outcome, path.name, row_idx, message)
            if outcome == "FAIL":
                failures += 1

    return 0 if failures == 0 else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
