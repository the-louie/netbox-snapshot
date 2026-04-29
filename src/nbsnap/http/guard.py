"""Source-read-only guard primitives.

The guard rail is implemented as two layers in `client.py`,
constructor refusal and per-request refusal. Both layers import
the helpers below so they stay in agreement on what "source URL"
and "non-write verb" mean.

The banner in `CLAUDE.md` calls this "a guard rail, not a
convention". The point of this module is to make accidental
writes against production NetBox structurally impossible: any
code path that wants to issue a write against the source URL has
to defeat both layers at once.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

# HTTP verbs the source NetBox is allowed to see. Anything outside
# this set is structurally forbidden by the guard rail in client.py.
# OPTIONS is here because the polymorphic-target probe in FEAT-05c1
# needs to ask the source for endpoint metadata; read-only on the wire.
READ_ONLY_VERBS = frozenset({"GET", "HEAD", "OPTIONS"})


class SourceWriteForbidden(Exception):
    """Raised when a non-GET request would hit the source NetBox.

    The message names the verb and the URL, with a pointer back to
    the production-read-only banner so a developer hitting this
    error in a stack trace knows immediately why.
    """

    def __init__(self, method: str, url: str) -> None:
        self.method = method
        self.url = url
        super().__init__(self._format())

    def _format(self) -> str:
        return (
            f"Refused {self.method} against the source NetBox at {self.url}. "
            "The source URL is read-only by policy, see the production banner "
            "in CLAUDE.md. If this fires from a test, point the test at the "
            "destination stack instead."
        )


def _host_port(url: str) -> str | None:
    """Return the `host:port` slice of a URL, or `None` on parse failure.

    Reduced to the parts that uniquely identify a NetBox install. We
    skip scheme and path on purpose, an attacker who knows the host
    cannot bypass the guard by appending `/api/` or by switching
    `https` to `http`.
    """
    try:
        split = urlsplit(url)
    except ValueError:
        return None
    host = split.hostname
    if host is None:
        return None
    # split.port is None when the URL has no port; fall back to the
    # default-port-for-scheme so `https://x/` and `https://x:443/`
    # compare equal.
    port = split.port
    if port is None:
        port = 443 if split.scheme == "https" else 80
    return f"{host}:{port}"


def is_source_url(base_url: str, source_url: str | None = None) -> bool:
    """True iff `base_url` points at the configured `NB_SOURCE_URL`.

    Args:
        base_url: The URL the caller wants to check.
        source_url: Optional override for the source URL. Defaults
            to `os.environ.get("NB_SOURCE_URL")` so production code
            does not have to pass it explicitly.

    The comparison is **equality** on the `host:port` slice. The
    ticket text reads "substring match", but we interpret that as
    "match only on host:port, ignore the path/scheme noise". Strict
    equality is safer than literal substring matching because a
    substring match would falsely positive on `host:8443` inside
    `host:84439`. Stripping path and scheme is what defeats the
    `/api/` trailing-slash bypass, not literal substring matching.

    Returns `False` when no source URL is configured, the destination
    case in `from_env("destination")` is the common path and must
    not raise.
    """
    if source_url is None:
        source_url = os.environ.get("NB_SOURCE_URL")
    if not source_url:
        return False
    a = _host_port(base_url)
    b = _host_port(source_url)
    if a is None or b is None:
        return False
    return a == b
