"""FEAT-01c pagination tests."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from nbsnap.http.client import NetboxHTTP


def _page(results: list[dict], next_url: str | None, count: int) -> MagicMock:
    """Build a mocked pagination page response."""

    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"x"
    resp.json.return_value = {"count": count, "next": next_url, "results": results}
    return resp


def _client_with(session: MagicMock) -> NetboxHTTP:
    return NetboxHTTP("https://dest.example/", "tok", session=session, page_size=2)


def test_get_all_follows_next_link_across_pages() -> None:
    """Three pages of two rows yield six rows in order."""

    session = MagicMock()
    session.request.side_effect = [
        _page([{"id": 1}, {"id": 2}], "https://dest.example/api/dcim/devices/?cursor=2", 6),
        _page([{"id": 3}, {"id": 4}], "https://dest.example/api/dcim/devices/?cursor=4", 6),
        _page([{"id": 5}, {"id": 6}], None, 6),
    ]
    client = _client_with(session)

    rows = list(client.get_all("dcim/devices/"))
    assert [r["id"] for r in rows] == [1, 2, 3, 4, 5, 6]


def test_get_all_warns_on_count_mismatch(caplog) -> None:
    """A `count` that disagrees with the yielded total logs a warning."""

    session = MagicMock()
    # Server claims 7 but only returns 4 rows total.
    session.request.side_effect = [
        _page([{"id": 1}, {"id": 2}], None, 7),
    ]
    session.request.return_value = _page([], None, 7)
    client = _client_with(session)

    with caplog.at_level(logging.WARNING, logger="nbsnap.http.client"):
        list(client.get_all("dcim/devices/"))
    assert any("count mismatch" in rec.message for rec in caplog.records)


def test_get_all_appends_limit_to_url() -> None:
    """The first request should carry `limit=<page_size>` in the URL."""

    session = MagicMock()
    session.request.side_effect = [_page([{"id": 1}], None, 1)]
    client = _client_with(session)

    list(client.get_all("dcim/devices/"))
    args, _ = session.request.call_args_list[0]
    assert "limit=2" in args[1]


def test_get_all_with_progress_yields_index_total_row() -> None:
    """`get_all_with_progress` returns `(index, total, row)` triples."""

    session = MagicMock()
    session.request.side_effect = [
        _page([{"id": 1}, {"id": 2}], None, 2),
    ]
    client = _client_with(session)

    triples = list(client.get_all_with_progress("dcim/devices/"))
    assert triples == [(1, 2, {"id": 1}), (2, 2, {"id": 2})]
