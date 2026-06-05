"""Unit tests for :mod:`nbsnap.http.exceptions` (ARCH-07a).

The exceptions in :mod:`nbsnap.http.exceptions` are the public surface
that the rest of the codebase will catch once ARCH-07b..e completes
the translation layer. We pin the hierarchy and the carried attributes
here so a future refactor cannot silently break a CLI catch clause.
"""

from __future__ import annotations

import pytest

from nbsnap.http import (
    SnapshotAuthError,
    SnapshotConnectivityError,
    SnapshotTransportError,
)


def test_subclass_hierarchy() -> None:
    """Auth and connectivity errors are SnapshotTransportError instances."""

    assert issubclass(SnapshotAuthError, SnapshotTransportError)
    assert issubclass(SnapshotConnectivityError, SnapshotTransportError)
    assert issubclass(SnapshotTransportError, RuntimeError)


def test_transport_error_carries_base_url_and_redirect_url() -> None:
    """Both contextual fields default to None and survive round-trip."""

    plain = SnapshotTransportError("oops")
    assert plain.base_url is None
    assert plain.redirect_url is None

    with_ctx = SnapshotTransportError(
        "refused redirect",
        base_url="https://dest.example/",
        redirect_url="http://attacker.example/",
    )
    assert with_ctx.base_url == "https://dest.example/"
    assert with_ctx.redirect_url == "http://attacker.example/"
    assert "refused redirect" in str(with_ctx)


def test_auth_error_carries_status() -> None:
    """SnapshotAuthError remembers the 401/403 status separately."""

    err = SnapshotAuthError(
        "token rejected", status=401, base_url="https://dest.example/"
    )
    assert err.status == 401
    assert err.base_url == "https://dest.example/"
    assert isinstance(err, SnapshotTransportError)


def test_connectivity_reason_literal_pinned() -> None:
    """The three documented values are the entire ConnectivityReason set.

    Locking the Literal set here makes a silent widening (someone
    adds ``"dns"`` without thinking about the CLI exit-code mapping
    in ARCH-07c) trip the test before it ships.
    """

    from typing import get_args

    from nbsnap.http.exceptions import ConnectivityReason

    assert set(get_args(ConnectivityReason)) == {"tls", "connection", "timeout"}


@pytest.mark.parametrize("reason", ["tls", "connection", "timeout"])
def test_connectivity_error_reason_round_trip(reason: str) -> None:
    """SnapshotConnectivityError preserves the reason discriminator."""

    err = SnapshotConnectivityError(
        f"could not reach {reason}", reason=reason, base_url="https://dest.example/"
    )
    assert err.reason == reason
    assert err.base_url == "https://dest.example/"


def test_transport_error_message_is_str_of_exception() -> None:
    """Operator-facing messages are accessible via str(exception)."""

    err = SnapshotTransportError("operator-friendly text")
    assert str(err) == "operator-friendly text"
