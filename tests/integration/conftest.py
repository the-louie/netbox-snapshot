"""Shared integration test fixtures.

The integration suite assumes the netbox-docker stacks at
`localhost:8080` (source) and `localhost:8081` (destination) are up,
seeded, and answering on `/api/status/`. When they are not, the
fixtures below skip the tests with a clear message so a developer
running the suite locally does not get a cryptic ConnectionError.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

# Reused across the tests, hoisted here so a tag bump in
# tests/fixtures/README.md does not require touching every test.
SOURCE_URL = "http://localhost:8080"
DEST_URL = "http://localhost:8081"
SOURCE_TOKEN = "0123456789abcdef0123456789abcdef01234567"
DEST_TOKEN = "abcdef0123456789abcdef0123456789abcdef01"


def _is_alive(url: str, token: str) -> bool:
    """Best-effort liveness probe; returns False on any error."""

    try:
        import requests

        resp = requests.get(
            f"{url}/api/status/",
            headers={"Authorization": f"Token {token}"},
            timeout=2,
        )
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 - any failure is "not alive"
        return False


@pytest.fixture(scope="session")
def stack_available() -> Iterator[bool]:
    """True iff both test stacks are answering."""

    available = _is_alive(SOURCE_URL, SOURCE_TOKEN) and _is_alive(DEST_URL, DEST_TOKEN)
    yield available


@pytest.fixture()
def require_stack(stack_available: bool) -> None:
    """Skip a test when the netbox-docker stack is not running."""

    if not stack_available:
        pytest.skip("netbox-docker stack is not running; start with `make stack-up stack-wait`")
