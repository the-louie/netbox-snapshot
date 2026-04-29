"""FEAT-01d retry envelope tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from nbsnap.http.client import NetboxHTTP, NetboxHTTPError, _parse_retry_after


def _resp(
    status: int, body: object | None = None, text: str = "", headers: dict | None = None
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = text
    resp.content = b"x" if status != 204 else b""
    resp.json.return_value = body if body is not None else {}
    return resp


def _client_with(session: MagicMock) -> NetboxHTTP:
    return NetboxHTTP("https://dest.example/", "tok", session=session, max_retries=3)


def test_503_then_200_retries_once() -> None:
    """One 503 followed by a 200 succeeds after a single retry."""

    session = MagicMock()
    session.request.side_effect = [_resp(503), _resp(200, {"ok": True})]
    client = _client_with(session)

    with patch("time.sleep") as sleep:
        result = client.get_one("status/")

    assert result == {"ok": True}
    assert session.request.call_count == 2
    sleep.assert_called_once_with(0.5)


def test_429_with_integer_retry_after_sleeps_that_long() -> None:
    """Integer `Retry-After` controls the sleep before the next try."""

    session = MagicMock()
    session.request.side_effect = [
        _resp(429, headers={"Retry-After": "1"}),
        _resp(200, {"ok": True}),
    ]
    client = _client_with(session)

    with patch("time.sleep") as sleep:
        client.get_one("status/")

    sleep.assert_called_once_with(1.0)


def test_429_with_http_date_retry_after_sleeps_until_target() -> None:
    """HTTP-date `Retry-After` is honoured per the Q9 burndown."""

    # Wed, 21 Oct 2026 07:28:00 GMT, with "now" mocked an hour earlier.
    session = MagicMock()
    session.request.side_effect = [
        _resp(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        _resp(200, {"ok": True}),
    ]
    client = _client_with(session)

    fake_now_str = "2026-10-21T06:28:00+00:00"
    with (
        patch("time.sleep") as sleep,
        patch("nbsnap.http.client.datetime") as dt_mod,
    ):
        from datetime import datetime, timezone

        dt_mod.now.return_value = datetime.fromisoformat(fake_now_str)
        dt_mod.fromisoformat = datetime.fromisoformat
        dt_mod.side_effect = datetime
        # Make `timezone` reachable through the patched module.
        dt_mod.timezone = timezone
        client.get_one("status/")

    # The single sleep should be approximately one hour.
    sleep.assert_called_once()
    (delay,), _ = sleep.call_args
    assert 3500 <= delay <= 3700


def test_400_does_not_retry() -> None:
    """4xx (non-429) is a hard error, raises after a single attempt."""

    session = MagicMock()
    session.request.side_effect = [_resp(400, text="bad input")]
    client = _client_with(session)

    with pytest.raises(NetboxHTTPError) as exc:
        client.get_one("dcim/devices/?bad=1")
    assert session.request.call_count == 1
    assert exc.value.status == 400


def test_five_503s_in_a_row_raises_after_three_retries() -> None:
    """Five consecutive 503s mean we try 4 times then raise."""

    session = MagicMock()
    session.request.side_effect = [_resp(503) for _ in range(5)]
    client = _client_with(session)

    with patch("time.sleep"), pytest.raises(NetboxHTTPError) as exc:
        client.get_one("status/")
    # original + 3 retries = 4 calls
    assert session.request.call_count == 4
    assert exc.value.status == 503


def test_connection_error_retries_then_raises() -> None:
    """A persistent `ConnectionError` bubbles up after the retry budget."""

    session = MagicMock()
    session.request.side_effect = [requests.ConnectionError("boom") for _ in range(4)]
    client = _client_with(session)

    with patch("time.sleep"), pytest.raises(requests.ConnectionError):
        client.get_one("status/")
    assert session.request.call_count == 4  # original + 3 retries


def test_parse_retry_after_handles_blank_and_garbage() -> None:
    """`_parse_retry_after` returns None for unparseable values."""

    assert _parse_retry_after(None) is None
    assert _parse_retry_after("") is None
    assert _parse_retry_after("not a date or number") is None
    assert _parse_retry_after("5") == 5.0


def test_parse_retry_after_http_date_far_future() -> None:
    """A far-future HTTP-date returns a positive number of seconds."""

    # Year 2099 is far enough that this test stays robust to clock
    # drift. The exact value matters less than the fact that the
    # parser returns a positive float in the right ballpark.
    seconds = _parse_retry_after("Wed, 01 Jan 2099 00:00:00 GMT")
    assert seconds is not None
    assert seconds > 0
    # Anchor: it should be at least many years (10 years ~ 3e8s).
    assert seconds > 1_000_000


def test_parse_retry_after_http_date_in_the_past_returns_zero() -> None:
    """A past HTTP-date clamps to zero rather than going negative."""

    seconds = _parse_retry_after("Thu, 01 Jan 1970 00:00:00 GMT")
    assert seconds == 0.0
