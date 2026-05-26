"""FEAT-37d bulk DELETE tests.

Three things this file pins:

1. Bulk DELETE issues one `DELETE /api/<endpoint>/` per batch
   with the array body NetBox 4.x expects.
2. When bulk fails (409 or any HTTP error), per-id DELETE
   fires for every row in the failed batch so a single bad
   row does not stall the rest of the batch.
3. `--on-error stop` aborts on first failure; `--on-error
   continue` accumulates and reports.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from nbsnap.http.client import NetboxHTTPError
from nbsnap.reset_cli import (
    BATCH,
    EXIT_DELETE_FAILURES,
    EXIT_OK,
    _bulk_delete,
    _chunks,
    _delete_ids,
    run_reset_cli,
)


def _args(**override) -> argparse.Namespace:
    defaults = {
        "url": "https://dest.example/",
        "token": "tok",
        "no_verify_tls": False,
        "content_types": "dcim.site",
        "keep": [],
        "apply": True,
        "confirmed": True,
        "on_error": "stop",
        "audit_out": None,
    }
    defaults.update(override)
    return argparse.Namespace(**defaults)


def _fake_client(rows_by_endpoint: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.is_source.return_value = False
    client.base_url = "https://dest.example/"
    rows = rows_by_endpoint or {}
    client.get_all.side_effect = lambda ep: iter(rows.get(ep, []))
    return client


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "NB_SOURCE_URL",
        "NB_SOURCE_TOKEN",
        "NB_DESTINATION_URL",
        "NB_DESTINATION_TOKEN",
        "NB_URL",
        "NB_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# _chunks
# ---------------------------------------------------------------------------


def test_chunks_yields_size_n_slices() -> None:
    assert list(_chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_chunks_handles_empty_list() -> None:
    assert list(_chunks([], 100)) == []


# ---------------------------------------------------------------------------
# _bulk_delete
# ---------------------------------------------------------------------------


def test_bulk_delete_posts_array_body() -> None:
    """The body shape is `[{"id": <int>}, ...]` per NetBox 4.x."""

    http = MagicMock()
    _bulk_delete(http, "dcim/sites/", [1, 2, 3])
    http._request.assert_called_once_with(
        "DELETE", "dcim/sites/", json=[{"id": 1}, {"id": 2}, {"id": 3}]
    )


# ---------------------------------------------------------------------------
# _delete_ids
# ---------------------------------------------------------------------------


def test_delete_ids_happy_path_uses_bulk() -> None:
    """A clean batch deletes via the bulk endpoint, no fallback."""

    http = MagicMock()
    http._request.return_value = None  # 204 No Content
    failures = _delete_ids(http, "dcim/sites/", [1, 2, 3])
    assert failures == []
    # One bulk call, no per-id DELETEs.
    assert http._request.call_count == 1


def test_delete_ids_falls_back_to_per_id_on_bulk_failure() -> None:
    """When bulk raises (e.g. 409), per-id DELETE fires for each
    row in the batch so the survivors still get deleted."""

    http = MagicMock()
    # First call (the bulk one) raises; subsequent per-id calls
    # all succeed.
    http._request.side_effect = [
        NetboxHTTPError("DELETE", "dcim/sites/", 409, "conflict"),
        None,  # per-id DELETE for id=1
        None,  # per-id DELETE for id=2
        None,  # per-id DELETE for id=3
    ]
    failures = _delete_ids(http, "dcim/sites/", [1, 2, 3])
    assert failures == []
    # 1 bulk + 3 per-id = 4 calls total.
    assert http._request.call_count == 4


def test_delete_ids_records_per_id_failures() -> None:
    """When per-id DELETE also fails, the id ends up in the
    failures list with the error message."""

    http = MagicMock()
    http._request.side_effect = [
        NetboxHTTPError("DELETE", "dcim/sites/", 409, "bulk-conflict"),
        NetboxHTTPError("DELETE", "dcim/sites/1/", 409, "still-conflict"),
        None,  # id=2 succeeds
    ]
    failures = _delete_ids(http, "dcim/sites/", [1, 2])
    assert len(failures) == 1
    failed_id, message = failures[0]
    assert failed_id == 1
    assert "still-conflict" in message


def test_delete_ids_does_not_per_id_retry_on_5xx() -> None:
    """A 500 from the destination means the server itself is
    unhappy, retrying per-id would dogpile the same error.
    The whole batch is marked failed without per-id retries."""

    http = MagicMock()
    http._request.side_effect = [
        NetboxHTTPError("DELETE", "dcim/sites/", 503, "service unavailable"),
    ]
    failures = _delete_ids(http, "dcim/sites/", [1, 2, 3])
    # All three ids ended up as failures via the 5xx path.
    assert len(failures) == 3
    assert all("503" in msg or "service unavailable" in msg for _, msg in failures)
    # Only the original bulk call fired; no per-id retries.
    assert http._request.call_count == 1


def test_delete_ids_chunks_at_batch_size() -> None:
    """Ids beyond BATCH split into multiple bulk calls."""

    http = MagicMock()
    http._request.return_value = None
    ids = list(range(1, BATCH + 3))  # BATCH + 2 ids
    _delete_ids(http, "dcim/sites/", ids)
    # Two bulk calls, one with BATCH ids, one with the remainder.
    assert http._request.call_count == 2


# ---------------------------------------------------------------------------
# _reverse_topological_order
# ---------------------------------------------------------------------------


def test_reverse_topological_order_falls_back_to_alphabetical() -> None:
    """When the planner cannot produce an order (e.g. NetBox is
    unreachable), the helper sorts the scope alphabetically so
    the operator at least gets deterministic behaviour."""

    from nbsnap.reset_cli import _reverse_topological_order

    http = MagicMock()
    # http.get_one raises immediately; OpenAPI.fetch propagates,
    # the except branch falls back to sorted(scope).
    http.get_one.side_effect = RuntimeError("schema unreachable")

    order = _reverse_topological_order(http, scope={"dcim.site", "dcim.device"})
    assert order == sorted({"dcim.site", "dcim.device"})


# ---------------------------------------------------------------------------
# Integration through run_reset_cli with --apply --i-know-what-im-doing
# ---------------------------------------------------------------------------


def test_run_reset_cli_apply_path_calls_bulk_delete() -> None:
    """End-to-end: an apply-confirmed run with two sites issues
    one bulk DELETE against `dcim/sites/` and exits 0."""

    http = _fake_client({"dcim/sites/": [
        {"id": 7, "name": "Hall-D"},
        {"id": 8, "name": "Hall-A"},
    ]})
    http._request.return_value = None  # 204
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=http):
        rc = run_reset_cli(_args())

    assert rc == EXIT_OK
    # Exactly one DELETE call (bulk) and it carries both ids.
    delete_calls = [c for c in http._request.mock_calls
                    if c.args and c.args[0] == "DELETE"]
    assert len(delete_calls) == 1
    body = delete_calls[0].kwargs["json"]
    assert body == [{"id": 7}, {"id": 8}]


def test_run_reset_cli_on_error_stop_aborts_first_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--on-error stop returns code 2 and stops processing
    further content types after the first per-id failure."""

    http = _fake_client({"dcim/sites/": [{"id": 1, "name": "A"}]})
    # Bulk fails, per-id also fails.
    http._request.side_effect = [
        NetboxHTTPError("DELETE", "dcim/sites/", 409, "bulk-conflict"),
        NetboxHTTPError("DELETE", "dcim/sites/1/", 409, "single-conflict"),
    ]
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=http):
        rc = run_reset_cli(_args(on_error="stop"))

    assert rc == EXIT_DELETE_FAILURES
    err = capsys.readouterr().err
    assert "STOP" in err


def test_run_reset_cli_on_error_continue_collects_failures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--on-error continue does not abort; it accumulates
    failures and reports the count in the trailer."""

    http = _fake_client({"dcim/sites/": [{"id": 1, "name": "A"}]})
    http._request.side_effect = [
        NetboxHTTPError("DELETE", "dcim/sites/", 409, "bulk-conflict"),
        NetboxHTTPError("DELETE", "dcim/sites/1/", 409, "single-conflict"),
    ]
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=http):
        rc = run_reset_cli(_args(on_error="continue"))

    assert rc == EXIT_DELETE_FAILURES
    err = capsys.readouterr().err
    assert "1 per-record failures" in err
