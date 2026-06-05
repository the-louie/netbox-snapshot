"""SEC-03a regression test: ``_send`` does not follow redirects.

Background
----------
Without ``allow_redirects=False`` the ``requests`` library follows a
``302 Location: http://attacker.example/`` and replays the
``Authorization: Token ...`` header against whatever host the
``Location`` points at. That is a high-severity token-leak path:
anyone who can write a 3xx into the destination response can
exfiltrate the token without a network sniff.

SEC-03a closed the gap by passing ``allow_redirects=False`` at the
leaf send and raising :class:`SnapshotTransportError` when a 3xx
response is observed. SEC-03b (a later ticket) adds an opt-in
one-hop helper that re-issues against the new URL only if the host
matches; until that lands, every 3xx is a hard refusal.

What we assert here
-------------------
* On a ``302`` response, exactly **one** request is issued, no
  follow-up against ``Location``.
* The raised ``SnapshotTransportError`` carries the
  ``redirect_url`` for the operator-facing message.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nbsnap.http.client import NetboxHTTP
from nbsnap.http.exceptions import SnapshotTransportError


def _redirect_response(location: str) -> MagicMock:
    """Build a 302 response mock with the supplied Location."""

    resp = MagicMock()
    resp.status_code = 302
    resp.headers = {"Location": location}
    resp.text = ""
    return resp


def test_send_does_not_follow_redirect() -> None:
    session = MagicMock()
    session.request.return_value = _redirect_response("http://attacker.example/")
    client = NetboxHTTP("https://dest.example/", "tok", session=session)

    with pytest.raises(SnapshotTransportError) as exc:
        client.get_one("status/")

    # The session must have been called exactly once. A second call
    # would mean `requests` (or our code) followed the redirect and
    # leaked the token to attacker.example.
    assert session.request.call_count == 1
    _, kwargs = session.request.call_args
    assert kwargs["allow_redirects"] is False

    assert exc.value.redirect_url == "http://attacker.example/"
    assert "refusing to follow" in str(exc.value)


def test_send_returns_2xx_normally() -> None:
    """Positive control: a 200 response is returned, not raised.

    SEC-03a touched the leaf send so a regression that turns every
    response into an exception is plausible. This test pins the
    happy path at the leaf so the 3xx-raise branch cannot drift
    into a 2xx-raise branch.
    """

    session = MagicMock()
    ok = MagicMock()
    ok.status_code = 200
    ok.headers = {}
    ok.text = ""
    ok.content = b"{}"
    session.request.return_value = ok
    client = NetboxHTTP("https://dest.example/", "tok", session=session)

    response = client._send("GET", "https://dest.example/api/status/")
    assert response is ok
    _, kwargs = session.request.call_args
    assert kwargs["allow_redirects"] is False


def test_send_raises_directly_on_3xx() -> None:
    """The 3xx raise lives at the leaf, not at the outer ``_request``.

    Calling ``_send`` directly with a mocked 302 must produce the
    same ``SnapshotTransportError``. This locks the contract so a
    future helper that bypasses ``_request`` (bulk POST in ARCH-03)
    still inherits the no-redirect guarantee.
    """

    session = MagicMock()
    session.request.return_value = _redirect_response("https://other.example/")
    client = NetboxHTTP("https://dest.example/", "tok", session=session)

    with pytest.raises(SnapshotTransportError) as exc:
        client._send("GET", "https://dest.example/api/status/")
    assert exc.value.redirect_url == "https://other.example/"


def test_send_redirect_without_location_header_still_refuses() -> None:
    """A 3xx with no Location is still refused, not silently consumed."""

    session = MagicMock()
    no_loc = MagicMock()
    no_loc.status_code = 301
    no_loc.headers = {}
    no_loc.text = ""
    session.request.return_value = no_loc
    client = NetboxHTTP("https://dest.example/", "tok", session=session)

    with pytest.raises(SnapshotTransportError) as exc:
        client.get_one("status/")

    assert exc.value.redirect_url is None
    assert exc.value.base_url == "https://dest.example/"
