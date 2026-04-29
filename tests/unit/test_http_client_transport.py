"""FEAT-01b transport tests, plus FEAT-01g2 guard layer 2."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nbsnap.http.client import NetboxHTTP, NetboxHTTPError
from nbsnap.http.guard import SourceWriteForbidden


def _mock_response(status: int = 200, body: object | None = None, text: str = "") -> MagicMock:
    """Build a `requests.Response`-shaped mock."""

    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body if body is not None else {}
    resp.text = text
    resp.content = b"x" if status != 204 else b""
    return resp


def _client_with_session(
    session: MagicMock, *, base_url: str = "https://dest.example/"
) -> NetboxHTTP:
    return NetboxHTTP(base_url, "tok", session=session)


def test_get_one_returns_parsed_json() -> None:
    """A 200 GET returns the parsed JSON body."""

    session = MagicMock()
    session.request.return_value = _mock_response(200, {"hello": "world"})
    client = _client_with_session(session)

    result = client.get_one("dcim/devices/1/")
    assert result == {"hello": "world"}
    session.request.assert_called_once()
    args, kwargs = session.request.call_args
    assert args[0] == "GET"
    assert args[1].endswith("/api/dcim/devices/1/")
    assert kwargs["headers"]["Authorization"] == "Token tok"


def test_post_returns_parsed_json() -> None:
    """A 201 POST returns the parsed JSON body."""

    session = MagicMock()
    session.request.return_value = _mock_response(201, {"id": 7})
    client = _client_with_session(session)

    result = client.post("dcim/sites/", {"name": "Hall D"})
    assert result == {"id": 7}


def test_patch_204_returns_none() -> None:
    """A 204 PATCH returns `None`."""

    session = MagicMock()
    session.request.return_value = _mock_response(204)
    client = _client_with_session(session)

    assert client.patch("dcim/devices/1/", {"name": "x"}) is None


def test_get_400_raises_with_body_in_message() -> None:
    """4xx response raises `NetboxHTTPError` carrying the body."""

    session = MagicMock()
    session.request.return_value = _mock_response(400, text="bad request, missing name")
    client = _client_with_session(session)

    with pytest.raises(NetboxHTTPError) as exc:
        client.get_one("dcim/devices/?bad=true")
    assert "bad request" in str(exc.value)
    assert exc.value.status == 400


def test_auth_header_set_on_every_request() -> None:
    """Authorization header is set on POST too, not just GET."""

    session = MagicMock()
    session.request.return_value = _mock_response(201, {})
    client = _client_with_session(session)

    client.post("dcim/sites/", {"name": "x"})
    _, kwargs = session.request.call_args
    assert kwargs["headers"]["Authorization"] == "Token tok"
    assert kwargs["headers"]["Content-Type"] == "application/json"


def test_get_all_returns_an_iterator() -> None:
    """`get_all` is implemented in FEAT-01c, returns an iterator."""

    from collections.abc import Iterator

    client = NetboxHTTP("https://dest.example/", "tok")
    result = client.get_all("dcim/devices/")
    # An iterator is fine for the type check, full behaviour tests
    # live in test_http_client_pagination.py.
    assert isinstance(result, Iterator)


def test_source_post_raises_before_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """FEAT-01g2 layer 2, source-bound POST raises pre-socket."""

    monkeypatch.setenv("NB_SOURCE_URL", "https://src.example/")
    session = MagicMock()
    client = NetboxHTTP("https://src.example/", "tok", session=session)
    assert client.is_source() is True

    with pytest.raises(SourceWriteForbidden):
        client.post("dcim/sites/", {"name": "x"})

    # Critically, the session must NOT have been touched.
    session.request.assert_not_called()


def test_source_get_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Source-bound GET is allowed and reaches the session."""

    monkeypatch.setenv("NB_SOURCE_URL", "https://src.example/")
    session = MagicMock()
    session.request.return_value = _mock_response(200, {"ok": True})
    client = NetboxHTTP("https://src.example/", "tok", session=session)

    result = client.get_one("status/")
    assert result == {"ok": True}
    session.request.assert_called_once()
