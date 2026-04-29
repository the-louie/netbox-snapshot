"""NetBox HTTP client with built-in source-readonly guard rail.

Two layers protect the source NetBox from accidental writes:

* **Constructor refusal.** When the configured `base_url` matches
  `NB_SOURCE_URL`, the constructor forces `allow_writes=False`
  regardless of the kwarg. The kwarg cannot override the source
  match. The intent is structural: a developer who instantiates a
  client against the production URL cannot accidentally end up
  with a writable handle.
* **Per-request refusal.** Before any socket activity, the request
  envelope checks the verb and raises `SourceWriteForbidden`
  for anything other than `GET`, `HEAD`, `OPTIONS` when bound to
  the source URL.

Both layers consult `nbsnap.http.guard` so the policy lives in one
place and the two layers stay in agreement.

The library underneath is `requests`, locked by RES-01
(`docs/implementation/01-http-client.md`).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

import requests

from nbsnap.config import load_dotenv
from nbsnap.http.guard import READ_ONLY_VERBS, SourceWriteForbidden, is_source_url

# Best-effort `.env` discovery so an import in test code finds the
# operator's tokens without forcing every test fixture to chdir.
# Failure is benign, the caller can still pass explicit values.
load_dotenv()

logger = logging.getLogger(__name__)

# Default backoff schedule, copied from
# `__reference/nb2kea/scripts/netbox_utils/netbox_common.py`. The
# 0.5/1.5/3.0 cadence is friction-10 M4. Used by FEAT-01d for the
# retry envelope; declared here so the constructor has the default
# value for its kwarg.
DEFAULT_BACKOFF: tuple[float, ...] = (0.5, 1.5, 3.0)


class NetboxHTTPError(RuntimeError):
    """Raised when the NetBox API responds with a 4xx or 5xx status.

    The message preserves the response body (truncated) so log lines
    are debuggable without re-issuing the call.
    """

    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(self._format())

    def _format(self) -> str:
        snippet = self.body if len(self.body) <= 500 else self.body[:497] + "..."
        return f"{self.method} {self.url} -> HTTP {self.status}: {snippet}"


# Type alias used by `from_env` so the role parameter is checked at
# call sites instead of stringly-typed.
Role = Literal["source", "destination"]


class NetboxHTTP:
    """Thin façade over `requests.Session` with the guard rail wired in.

    Constructed directly when a caller already has the URL and token
    in hand, or via `NetboxHTTP.from_env(role)` for the standard
    four-variable env scheme documented in `CLAUDE.md`.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30,
        verify_tls: bool = True,
        page_size: int = 500,
        max_retries: int = 3,
        backoff: tuple[float, ...] = DEFAULT_BACKOFF,
        allow_writes: bool = True,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url
        self._token = token
        self._timeout = timeout
        self._verify_tls = verify_tls
        self._page_size = page_size
        self._max_retries = max_retries
        self._backoff = backoff

        # Guard layer 1, constructor refusal.
        self._is_source = is_source_url(base_url)
        if self._is_source and allow_writes:
            logger.info("read-only client bound to source NetBox at %s", base_url)
            allow_writes = False
        self._allow_writes = allow_writes

        if not verify_tls:
            # The local self-signed source endpoint needs this. We
            # log once at construction time so the operator sees the
            # posture; suppression of urllib3's per-request warning
            # lives in FEAT-01e.
            logger.warning("TLS verification disabled for %s", base_url)

        # Dependency-injection seam, tests pass a mock Session so the
        # transport tests do not require a live socket.
        self._session = session if session is not None else requests.Session()

    # ------------------------------------------------------------------
    # Public diagnostics
    # ------------------------------------------------------------------
    def is_source(self) -> bool:
        """True iff this client is bound to the configured source URL."""
        return self._is_source

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def allow_writes(self) -> bool:
        return self._allow_writes

    @property
    def page_size(self) -> int:
        return self._page_size

    def __repr__(self) -> str:
        # Mask the token so a debug print does not leak it. Show the
        # last 4 chars for "is this the right token" sanity, the
        # leading bytes stay hidden.
        masked = f"***{self._token[-4:]}" if self._token else "***"
        return (
            f"NetboxHTTP(base_url={self._base_url!r}, token={masked}, "
            f"verify_tls={self._verify_tls}, allow_writes={self._allow_writes}, "
            f"is_source={self._is_source})"
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def from_env(
        cls,
        role: Role,
        *,
        url: str | None = None,
        token: str | None = None,
        **overrides: Any,
    ) -> NetboxHTTP:
        """Resolve URL + token from the env, return a constructed client.

        Resolution order, in decreasing precedence:

        1. Explicit `url` / `token` kwargs.
        2. Role-specific env var (`NB_SOURCE_URL`, `NB_DESTINATION_TOKEN`, ...).
        3. Legacy `NB_URL` / `NB_TOKEN` from the nb2kea project.

        When `role == "source"`, the explicit `allow_writes=False`
        is set so the intent reads at call sites, even though the
        constructor would force it anyway via the guard layer.
        """
        role_url = f"NB_{role.upper()}_URL"
        role_token = f"NB_{role.upper()}_TOKEN"

        resolved_url = url or os.environ.get(role_url) or os.environ.get("NB_URL")
        resolved_token = token or os.environ.get(role_token) or os.environ.get("NB_TOKEN")

        if not resolved_url:
            msg = f"no URL configured, set --url or export {role_url} (or the legacy NB_URL)"
            raise ValueError(msg)
        if not resolved_token:
            msg = (
                f"no token configured, set --token or export {role_token} (or the legacy NB_TOKEN)"
            )
            raise ValueError(msg)

        if role == "source":
            # Explicit so the intent shows up at call sites. The
            # constructor would force this anyway via the guard
            # rail, but visibility matters.
            overrides.setdefault("allow_writes", False)

        return cls(resolved_url, resolved_token, **overrides)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_url(self, path: str) -> str:
        """Compose the absolute URL for an API call.

        Accepts `path` with or without a leading slash, and with or
        without the `api/` prefix so call sites can pick whichever
        spelling reads best.
        """
        normalised = path.lstrip("/")
        if not normalised.startswith("api/"):
            normalised = f"api/{normalised}"
        return f"{self._base_url.rstrip('/')}/{normalised}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._token}",
            "Accept": "application/json",
        }

    def _enforce_readonly(self, method: str) -> None:
        """Guard layer 2, raise before any socket activity on writes."""
        if self._is_source and method.upper() not in READ_ONLY_VERBS:
            raise SourceWriteForbidden(method.upper(), self._base_url)

    def _request(self, method: str, path: str, *, json: Any = None) -> Any:
        """Run a single request, return parsed JSON or `None` on 204.

        Retries are layered on top in FEAT-01d; this method stays
        the single transport seam.
        """
        self._enforce_readonly(method)
        url = self._build_url(path)
        headers = self._headers()
        if json is not None:
            headers["Content-Type"] = "application/json"

        response = self._session.request(
            method.upper(),
            url,
            headers=headers,
            json=json,
            timeout=self._timeout,
            verify=self._verify_tls,
        )
        if response.status_code >= 400:
            raise NetboxHTTPError(method.upper(), url, response.status_code, response.text)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------------
    # Public verb wrappers
    # ------------------------------------------------------------------
    def get_one(self, path: str) -> Any:
        """Issue a single GET, return parsed JSON."""
        return self._request("GET", path)

    def post(self, path: str, body: Any) -> Any:
        """Issue a POST, return parsed JSON or None."""
        return self._request("POST", path, json=body)

    def patch(self, path: str, body: Any) -> Any:
        """Issue a PATCH, return parsed JSON or None."""
        return self._request("PATCH", path, json=body)

    def get_all(self, path: str) -> Any:  # noqa: ARG002
        """Iterate every page of a list endpoint.

        Pagination is implemented in FEAT-01c, so this raises until
        that ticket lands.
        """
        msg = "get_all is implemented in FEAT-01c"
        raise NotImplementedError(msg)
