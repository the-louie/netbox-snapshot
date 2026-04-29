"""FEAT-01f page-size shrink-on-timeout tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import requests

from nbsnap.http.client import NetboxHTTP


def _page(rows: list[dict], next_url: str | None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"x"
    resp.json.return_value = {"count": len(rows), "next": next_url, "results": rows}
    return resp


def test_pagination_shrinks_on_timeout_until_success() -> None:
    """500 fails, 200 fails, 50 succeeds, cached page size becomes 50."""

    session = MagicMock()
    session.request.side_effect = [
        requests.Timeout("at 500"),
        requests.Timeout("at 200"),
        _page([{"id": 1}], None),
    ]
    client = NetboxHTTP("https://dest.example/", "tok", session=session, page_size=500)

    rows = list(client.get_all("dcim/devices/"))
    assert rows == [{"id": 1}]
    assert session.request.call_count == 3

    # The three URLs should carry limit=500, limit=200, limit=50.
    urls = [c.args[1] for c in session.request.call_args_list]
    assert "limit=500" in urls[0]
    assert "limit=200" in urls[1]
    assert "limit=50" in urls[2]

    # The cached page size should now be 50 so the rest of the run
    # avoids the doomed 500 retry.
    assert client.page_size == 50
