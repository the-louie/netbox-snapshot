"""End-to-end source-readonly guard test (FEAT-01g3).

The two-layer guard rail must refuse non-GET requests against the
source URL **before** any network activity. This test instruments
`socket.socket.__init__` with a counter and asserts the counter
stays at zero for every refused verb.

The test is marked `integration` because it builds a real
`NetboxHTTP` against `NB_SOURCE_URL`. It does **not** require the
source NetBox to be reachable, the whole point is that the guard
rail fires before the client tries to open a connection.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from nbsnap.http.client import NetboxHTTP
from nbsnap.http.exceptions import SnapshotConnectivityError
from nbsnap.http.guard import SourceWriteForbidden


@pytest.fixture()
def socket_counter(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """Wrap `socket.socket.__init__` with a counter list.

    Returning a one-element list (effectively a mutable int box)
    lets tests observe the counter without having to thread a
    `nonlocal` through a fixture.
    """

    counter = [0]
    original = socket.socket.__init__

    def patched(self: socket.socket, *args: object, **kwargs: object) -> None:
        counter[0] += 1
        original(self, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "__init__", patched)
    yield counter


@pytest.fixture()
def source_client(monkeypatch: pytest.MonkeyPatch) -> NetboxHTTP:
    """Build a `NetboxHTTP` bound to a synthetic source URL.

    We avoid `host.docker.internal` here so the test does not
    depend on docker. The URL still trips the source detection
    because we point `NB_SOURCE_URL` at the same value.
    """

    monkeypatch.setenv("NB_SOURCE_URL", "https://prod.example:8443/")
    client = NetboxHTTP("https://prod.example:8443/", "tok", verify_tls=False)
    assert client.is_source() is True
    return client


@pytest.mark.parametrize("verb", ["POST", "PATCH", "PUT", "DELETE"])
def test_write_verb_raises_before_socket_open(
    source_client: NetboxHTTP, socket_counter: list[int], verb: str
) -> None:
    """POST/PATCH/PUT/DELETE against the source URL never reach a socket."""

    # Reset the counter so we ignore any sockets opened during
    # fixture set-up (none should, but be defensive).
    socket_counter[0] = 0

    with pytest.raises(SourceWriteForbidden):
        # POST and PATCH have public wrappers; PUT and DELETE go
        # through _request directly because the client does not
        # expose them as named methods.
        if verb == "POST":
            source_client.post("dcim/sites/", {"name": "test"})
        elif verb == "PATCH":
            source_client.patch("dcim/sites/1/", {"name": "test"})
        else:
            source_client._request(verb, "dcim/sites/", json={"name": "test"})

    assert socket_counter[0] == 0, f"{verb} against the source URL must not open a socket"


def test_get_is_allowed_against_source(source_client: NetboxHTTP) -> None:
    """A GET against the source IS allowed, even when it ends up failing.

    We expect a `SnapshotConnectivityError` (the ARCH-07b wrapper
    over the underlying `requests.ConnectionError` raised when the
    synthetic URL does not resolve). The key assertion is that the
    failure is from the network layer, NOT from
    `SourceWriteForbidden`, the guard rail does not refuse GET.
    """

    with pytest.raises(SnapshotConnectivityError):
        source_client.get_one("status/")
