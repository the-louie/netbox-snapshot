"""FEAT-01e TLS verification toggle tests."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from nbsnap.http.client import NetboxHTTP


def test_verify_tls_false_logs_warning(caplog) -> None:
    """`verify_tls=False` emits an INFO/WARNING line at construction."""

    with caplog.at_level(logging.WARNING, logger="nbsnap.http.client"):
        NetboxHTTP("https://dest.example/", "tok", verify_tls=False)
    assert any("TLS verification disabled" in rec.message for rec in caplog.records)


def test_verify_tls_true_does_not_log_warning(caplog) -> None:
    """`verify_tls=True` (default) emits no TLS warning."""

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nbsnap.http.client"):
        NetboxHTTP("https://dest.example/", "tok", verify_tls=True)
    assert not any("TLS verification disabled" in rec.message for rec in caplog.records)


def test_session_call_carries_verify_flag() -> None:
    """The `verify` kwarg is plumbed through to the underlying session."""

    session = MagicMock()
    session.request.return_value = MagicMock(status_code=200, content=b"x")
    session.request.return_value.json.return_value = {"ok": True}

    client = NetboxHTTP("https://dest.example/", "tok", session=session, verify_tls=False)
    client.get_one("status/")
    _, kwargs = session.request.call_args
    assert kwargs["verify"] is False
