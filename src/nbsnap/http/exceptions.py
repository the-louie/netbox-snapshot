"""Domain-level exceptions for the HTTP transport layer (ARCH-07).

Why this module exists
----------------------
Before ARCH-07, the rest of ``nbsnap`` caught ``requests.exceptions.*``
directly (search the repository for ``import requests`` outside this
package and you find CLI shells, the graph builder, and a few
helpers). That tied the entire codebase to the third-party transport
library and made it impossible to swap the HTTP stack without a
sprawling diff.

The exceptions in this module sit one layer above ``requests`` and
re-express the failures that nbsnap actually wants to react to:

* :class:`SnapshotTransportError` is the umbrella for anything that
  went wrong on the wire.
* :class:`SnapshotAuthError` is the 401/403 case where the operator's
  token is the problem.
* :class:`SnapshotConnectivityError` is the DNS/TLS/socket case where
  the destination is unreachable.

The translation layer that converts ``requests.exceptions`` and
non-2xx responses into these types lives in :func:`nbsnap.http.client`
(landed in ARCH-07b). Call sites outside ``nbsnap.http`` should only
ever see, and catch, the exceptions defined here. ARCH-07e adds a
regression test that walks the source tree to enforce that boundary.

Design notes for downstream catchers
------------------------------------
* All three classes carry a ``base_url`` attribute so operator-facing
  messages can name the endpoint without re-reading state from a
  client.
* :class:`SnapshotAuthError` carries the HTTP ``status`` (401 or 403)
  so the CLI can distinguish between bad-token and forbidden-resource
  without re-parsing the message.
* :class:`SnapshotConnectivityError` carries a ``reason`` discriminator
  (``"tls"``, ``"connection"``, ``"timeout"``) so the CLI can emit a
  precise hint (renew the certificate vs. check the firewall).
"""

from __future__ import annotations

from typing import Literal

# Discriminator values used by SnapshotConnectivityError. Listed here as
# a Literal so downstream code can pattern-match exhaustively and so
# typos surface at the type checker, not at runtime.
ConnectivityReason = Literal["tls", "connection", "timeout"]


class SnapshotTransportError(RuntimeError):
    """Umbrella exception for any wire-level failure in nbsnap.

    Concrete subclasses tell the caller what category of failure it
    is. Plain ``SnapshotTransportError`` instances are also valid for
    cases that do not fit a finer category (e.g. an unexpected 3xx
    redirect, see SEC-03a, or a malformed JSON body that the client
    chooses not to retry).

    Parameters
    ----------
    message:
        Operator-facing description. Should already contain the
        relevant context (URL, status, reason). Avoid raw library
        exception strings, they leak ``requests`` implementation
        details into the operator transcript.
    base_url:
        The ``base_url`` of the client that raised. Optional so
        callers that re-raise from a context where the client is not
        in scope (e.g. utility helpers) can still construct.
    redirect_url:
        Set when the failure is a refused redirect (see SEC-03a).
        ``None`` when the failure is not a redirect.

        Carrying the redirect URL on the base (rather than a fourth
        subclass) lets call sites use the natural ``except
        SnapshotTransportError as e: if e.redirect_url: ...`` idiom
        without having to import an extra exception type. The trade
        off is intentional: redirect failures are rare and the
        attribute is harmless when ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        base_url: str | None = None,
        redirect_url: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.redirect_url = redirect_url
        super().__init__(message)


class SnapshotAuthError(SnapshotTransportError):
    """Authentication or authorization failure against a NetBox endpoint.

    Raised on HTTP 401 (no/invalid token) and HTTP 403 (token valid
    but the user lacks permission for the requested object).

    The ``status`` attribute lets the CLI branch on the failure mode
    without re-parsing the message; the CLI's error path uses it to
    print "renew the token" vs. "ask an admin to grant permission".
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        base_url: str | None = None,
    ) -> None:
        self.status = status
        super().__init__(message, base_url=base_url)


class SnapshotConnectivityError(SnapshotTransportError):
    """The destination NetBox was unreachable at the wire level.

    The ``reason`` discriminator separates the three common causes:

    * ``"tls"`` for a certificate validation failure (the operator
      either needs ``--no-verify-tls`` or has to renew the cert).
    * ``"connection"`` for DNS, refused, or reset-by-peer (firewall
      or wrong host).
    * ``"timeout"`` for a socket that opened but never responded
      within the configured timeout.

    The CLI maps these to distinct exit codes and hints so the
    operator's first diagnostic step is the right one.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: ConnectivityReason,
        base_url: str | None = None,
    ) -> None:
        self.reason = reason
        super().__init__(message, base_url=base_url)


__all__ = [
    "ConnectivityReason",
    "SnapshotAuthError",
    "SnapshotConnectivityError",
    "SnapshotTransportError",
]
