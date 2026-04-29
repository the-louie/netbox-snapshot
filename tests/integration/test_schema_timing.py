"""TEST-01a, schema fetch timing assertion against the test stack."""

from __future__ import annotations

import threading
import time

import pytest

from nbsnap.http.client import NetboxHTTP
from nbsnap.schema.content_types import ContentTypeCache
from nbsnap.schema.openapi import OpenAPI
from nbsnap.schema.status import Status

from .conftest import DEST_TOKEN, DEST_URL, SOURCE_TOKEN, SOURCE_URL


def _fetch_all(url: str, token: str) -> float:
    """Fetch OpenAPI + content types + status; return wall-clock seconds."""

    http = NetboxHTTP(url, token, verify_tls=False, timeout=30)
    start = time.perf_counter()
    OpenAPI.fetch(http)
    ContentTypeCache.fetch(http)
    Status.fetch(http)
    return time.perf_counter() - start


@pytest.mark.usefixtures("require_stack")
def test_each_stack_fetches_schema_under_thirty_seconds() -> None:
    """Phase-1 exit criterion: full schema fetch in <30 seconds."""

    results: dict[str, float] = {}

    def runner(name: str, url: str, token: str) -> None:
        results[name] = _fetch_all(url, token)

    threads = [
        threading.Thread(target=runner, args=("source", SOURCE_URL, SOURCE_TOKEN)),
        threading.Thread(target=runner, args=("dest", DEST_URL, DEST_TOKEN)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results["source"] < 30, f"source fetch took {results['source']:.2f}s"
    assert results["dest"] < 30, f"dest fetch took {results['dest']:.2f}s"
