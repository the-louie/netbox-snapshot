"""SEC-03c: import flow refuses a 302 redirect across hosts.

A mocked destination returns ``302 Location: http://attacker.example/``
on the first request. Two things must hold:

1. No second request goes out. The redirect is refused at the
   :func:`nbsnap.http.client._send` leaf (SEC-03a) and the
   ``Authorization: Token`` header therefore never reaches the
   attacker host.
2. The CLI surfaces a recognisable error message, not a silent
   success or a partial run.

The test stays at the HTTP-client level, the lab destination is
not required and we do not need to drive the full preflight.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nbsnap.http.client import NetboxHTTP
from nbsnap.http.exceptions import SnapshotTransportError


def _build_302_to_attacker() -> MagicMock:
    """Forge a ``requests``-shaped 302 pointing at a cross-host URL."""

    resp = MagicMock()
    resp.status_code = 302
    resp.headers = {"Location": "http://attacker.example/api/"}
    resp.text = ""
    return resp


def test_first_get_is_refused_and_no_second_request_goes_out() -> None:
    """A real-shaped 302 on the import side, refused at the leaf send."""

    session = MagicMock()
    session.request.return_value = _build_302_to_attacker()
    client = NetboxHTTP("https://dest.example/", "secret-token", session=session)

    with pytest.raises(SnapshotTransportError) as exc:
        client.get_one("dcim/devices/?limit=1")

    # The session must have been called exactly once, the second
    # call (which would have replayed the Authorization: Token
    # header against attacker.example) never happens.
    assert session.request.call_count == 1
    assert exc.value.redirect_url == "http://attacker.example/api/"

    # Defence in depth: the failing request carried the
    # Authorization header (we only need to check the args of the
    # one call that happened, the next one never happened so the
    # token is not on the wire toward attacker.example).
    args, kwargs = session.request.call_args
    assert kwargs["headers"]["Authorization"].startswith("Token ")
    # session.request signature: (method, url, **kwargs); the URL is
    # positional. Confirm we never reached for attacker.example.
    assert "attacker.example" not in args[1]
