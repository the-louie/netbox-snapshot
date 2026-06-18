"""Shared integration test fixtures.

The integration suite assumes the netbox-docker stacks at
`localhost:8080` (source) and `localhost:8081` (destination) are up,
seeded, and answering on `/api/status/`. When they are not, the
fixtures below skip the tests with a clear message so a developer
running the suite locally does not get a cryptic ConnectionError.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

# Reused across the tests, hoisted here so a tag bump in
# tests/fixtures/README.md does not require touching every test.
SOURCE_URL = "http://localhost:8080"
DEST_URL = "http://localhost:8081"
SOURCE_TOKEN = "0123456789abcdef0123456789abcdef01234567"
DEST_TOKEN = "abcdef0123456789abcdef0123456789abcdef01"


def _is_alive(url: str, token: str) -> bool:
    """Best-effort liveness probe; returns False on any error.

    Retries up to three times with a one second pause between
    attempts. NetBox right after `make stack-bootstrap` can be
    warm but still slow on the first call from a new client; a
    single 2s timeout was producing false negatives that
    session-cached and skipped every `require_stack` test.
    Surface the last failure cause via `print` so a CI log shows
    what the probe actually saw if it ever returns False.
    """

    import requests

    last_err: str = "no attempts made"
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{url}/api/status/",
                headers={"Authorization": f"Token {token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return True
            last_err = f"HTTP {resp.status_code}: {resp.text[:120]!r}"
        except Exception as exc:  # noqa: BLE001 - any failure is "not alive"
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < 2:
            time.sleep(1.0)
    print(f"stack probe FAILED for {url}: {last_err}")
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
