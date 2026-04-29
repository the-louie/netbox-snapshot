"""FEAT-01g2 client `is_source` integration tests."""

from __future__ import annotations

import pytest

from nbsnap.http.client import NetboxHTTP


@pytest.fixture()
def _source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NB_SOURCE_URL", "https://host.docker.internal:8443")


def test_construction_against_source_marks_is_source(_source_env: None) -> None:  # noqa: ARG001
    """Constructor flags `is_source` when bound to source URL."""

    client = NetboxHTTP("https://host.docker.internal:8443/api/", "tok")
    assert client.is_source() is True
    assert "is_source=True" in repr(client)


def test_construction_against_destination_is_not_source(_source_env: None) -> None:  # noqa: ARG001
    """Destination URL does not set the source flag."""

    client = NetboxHTTP("https://netbox.i.louie.se/", "tok")
    assert client.is_source() is False


def test_allow_writes_kwarg_does_not_override_source_detection(
    _source_env: None,  # noqa: ARG001
) -> None:
    """The constructor kwarg `allow_writes=True` is silently downgraded."""

    client = NetboxHTTP("https://host.docker.internal:8443/", "tok", allow_writes=True)
    assert client.allow_writes is False
