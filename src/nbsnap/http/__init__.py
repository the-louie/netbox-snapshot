"""HTTP client and source-read-only guard rail primitives.

Public surface
--------------
The HTTP package exposes a thin nbsnap-domain API; callers outside
``nbsnap.http`` should import from here, never from the third-party
``requests`` library directly. ARCH-07e enforces this boundary with
a regression test.

Re-exports:

* :class:`NetboxHTTP`: the session wrapper with the source-readonly
  guard and the retry/backoff envelope.
* :class:`NetboxHTTPError`: legacy 4xx/5xx error type. Will be
  replaced by the domain exceptions below over the ARCH-07b..e
  rollout; kept here for the migration window.
* :class:`SnapshotTransportError` and subclasses
  (:class:`SnapshotAuthError`, :class:`SnapshotConnectivityError`):
  the nbsnap-domain failures that downstream callers should catch.
* :class:`SourceWriteForbidden`: the guard-layer exception, raised
  when a write verb is attempted against the source NetBox.
"""

from nbsnap.http.client import NetboxHTTP, NetboxHTTPError
from nbsnap.http.exceptions import (
    ConnectivityReason,
    SnapshotAuthError,
    SnapshotConnectivityError,
    SnapshotTransportError,
)
from nbsnap.http.guard import SourceWriteForbidden

__all__ = [
    "ConnectivityReason",
    "NetboxHTTP",
    "NetboxHTTPError",
    "SnapshotAuthError",
    "SnapshotConnectivityError",
    "SnapshotTransportError",
    "SourceWriteForbidden",
]
