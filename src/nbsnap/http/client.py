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

import email.utils
import logging
import os
import re
import time
import warnings
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlencode, urlparse, urlsplit, urlunsplit

import requests

from nbsnap.config import load_dotenv
from nbsnap.http.exceptions import (
    SnapshotAuthError,
    SnapshotConnectivityError,
    SnapshotTransportError,
)
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

# Page-shrink cascade for FEAT-01f. When a paginated GET times out
# at the current page size, the client halves the limit (clamped to
# the next entry in this list) and retries. Floor is 25, below that
# the problem is not page size, it is the server.
SHRINK_LADDER: tuple[int, ...] = (500, 200, 50, 25)


# Module-level "warning already fired" sentinel for the TLS-off
# message. Suppressing urllib3's per-request InsecureRequestWarning
# is done exactly once at import time when the operator first asks
# for an insecure session, so log volume stays sane.
_TLS_WARNING_SUPPRESSED = False


def _suppress_insecure_request_warning() -> None:
    """Silence urllib3's per-request `InsecureRequestWarning`, once."""
    global _TLS_WARNING_SUPPRESSED
    if _TLS_WARNING_SUPPRESSED:
        return
    try:
        from urllib3.exceptions import InsecureRequestWarning

        warnings.filterwarnings("ignore", category=InsecureRequestWarning)
    except ImportError:  # pragma: no cover, urllib3 always ships with requests
        return
    _TLS_WARNING_SUPPRESSED = True


def _parse_retry_after(value: str | None) -> float | None:
    """Convert a `Retry-After` header into seconds-to-sleep.

    Two RFC forms are supported, per friction-10 Q9 burndown:
    integer seconds (`Retry-After: 5`) and HTTP-date
    (`Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`). Anything else
    returns `None` so the caller falls back to the backoff schedule.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    # Integer seconds first, that's the common case.
    try:
        return max(0.0, float(stripped))
    except ValueError:
        pass
    # HTTP-date, anchor to "now in UTC" so the subtraction is sane.
    try:
        target = email.utils.parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    return max(0.0, (target - now).total_seconds())


# SEC-05a body-redaction patterns. Compiled once at module scope so
# repeated NetboxHTTPError construction does not pay the regex
# compilation cost. The three patterns map 1:1 to the three audit
# leak channels the security audit identified:
#
# * ``Authorization:`` header echoes that appear in a NetBox error
#   page (the destination occasionally renders request headers in
#   its 500 template);
# * Bare ``Token <hex>`` mentions in JSON bodies (NetBox does not do
#   this today but a 3rd-party plugin might);
# * Inline ``<script>`` / ``<style>`` blocks from an HTML error page
#   that would otherwise paste base64 secrets or external URLs into
#   audit.jsonl.
_AUTH_HEADER_LINE = re.compile(r"^\s*Authorization:.*$", re.IGNORECASE | re.MULTILINE)
_TOKEN_LITERAL = re.compile(r"Token\s+[0-9a-fA-F]+")
_SCRIPT_OR_STYLE_BLOCK = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Note on the script/style regex: an unterminated ``<script>`` block
# (no closing ``</script>``) does NOT match and would therefore leak
# through. NetBox's own error pages always emit well-formed HTML, so
# this is acceptable. If a future deployment puts a malformed proxy
# in front of NetBox we will need an additional "tag-opens-without-
# close" pattern.


def _host_port(url: str) -> tuple[str, int]:
    """Return ``(hostname, port)`` from ``url``, with scheme-default port.

    Used by :meth:`NetboxHTTP._follow_one_safe_hop` (SEC-03b) to
    compare a redirect's ``Location`` against the client's
    ``base_url``. ``urlparse.port`` is None when the URL omits the
    port; in that case we substitute the scheme's default (443 for
    HTTPS, 80 otherwise) so ``https://x/`` and ``https://x:443/``
    compare equal.
    """

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.port is not None:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    return host, port


def _redact_body(body: str) -> str:
    """Strip secrets-shaped content from a response body before logging.

    Three transformations run in order:

    1. Any line beginning ``Authorization:`` (case-insensitive) is
       replaced wholesale, the operator's token can land verbatim
       in a destination's debug response and audit.jsonl would
       otherwise record it.
    2. Any literal ``Token <hex>`` substring is masked to
       ``Token <redacted>``.
    3. Any ``<script>`` or ``<style>`` block is stripped, an HTML
       error page from a misconfigured proxy can otherwise leak
       cookies or analytics URLs into the audit log.

    The function is intentionally lossy. The audit log is forensic,
    not a faithful response capture; preserving the rest of the body
    is enough to debug a failure without echoing secrets.
    """

    redacted = _AUTH_HEADER_LINE.sub("Authorization: <redacted>", body)
    redacted = _TOKEN_LITERAL.sub("Token <redacted>", redacted)
    redacted = _SCRIPT_OR_STYLE_BLOCK.sub("", redacted)
    return redacted


class NetboxHTTPError(RuntimeError):
    """Raised when the NetBox API responds with a 4xx or 5xx status.

    The message preserves the response body (truncated) so log lines
    are debuggable without re-issuing the call. SEC-05b routes the
    body through :func:`_redact_body` at construction time so both
    ``str(error)`` and downstream consumers (audit.jsonl, stderr
    handlers) see the already-sanitised string. There is no separate
    "raw body" channel; if the caller needs the literal response it
    must inspect the underlying :class:`requests.Response` before
    constructing this exception.
    """

    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        self.method = method
        self.url = url
        self.status = status
        self.body = _redact_body(body)
        super().__init__(self._format())

    def _format(self) -> str:
        # The body is already SEC-05b-sanitised at construction, the
        # truncation is purely a transcript-noise control. A 500-char
        # ceiling matters here because the body has already had
        # secrets stripped, so what we keep is debugging info, not
        # raw response bytes.
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
            # posture, and silence urllib3's per-request warning so
            # the operator's terminal does not turn into noise.
            logger.warning("TLS verification disabled for %s", base_url)
            _suppress_insecure_request_warning()

        # Dependency-injection seam, tests pass a mock Session so the
        # transport tests do not require a live socket.
        self._session = session if session is not None else requests.Session()

        # Instance-scoped custom-field cache (REFACTOR-06). The
        # cache used to be a module global keyed by base_url,
        # which meant a test that targeted a new base URL and
        # forgot to clear it could mask stale-cache regressions.
        # Keying off the instance makes each NetboxHTTP a clean
        # slate without ceremony.
        self._cf_cache: dict[str, set[str]] | None = None
        self._cf_cache_failed: bool = False
        # BUG-03: signals that the destination's customfield
        # phase has run. While False, the CF filter does not
        # strip keys; an empty cache could just mean the
        # customfield definitions have not landed yet.
        self._cf_phase_complete: bool = False

    def clear_cf_cache(self) -> None:
        """Drop the per-instance custom-field cache.

        Call this after a custom-field upsert phase so subsequent
        record upserts re-read the destination's updated
        custom-field registry.
        """
        self._cf_cache = None
        self._cf_cache_failed = False

    def mark_cf_phase_complete(self) -> None:
        """Signal that the customfield phase finished.

        Subsequent calls to `_known_custom_fields_for` will
        return concrete sets (possibly empty) instead of the
        "do not filter" None sentinel. The driver calls this
        after the `extras.customfield` content type phase
        finishes, see BUG-03.
        """
        self._cf_phase_complete = True
        # Force a re-read so the next lookup sees the
        # definitions the phase just landed.
        self.clear_cf_cache()

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

    def _send(
        self, method: str, url: str, *, json: Any = None, timeout: float | None = None
    ) -> requests.Response:
        """Single send through the session, no retries.

        Kept separate from `_request` so the retry envelope wraps a
        thin atomic call.

        The source-readonly guard runs here at the leaf, **not** in the
        outer `_request` or `get_all`. SEC-02a moved the check down so
        every code path that ends up calling the socket goes through
        the same enforcement, including direct test calls and any
        future helper that bypasses `_request` (e.g. bulk endpoints
        from ARCH-03). The cost is one dict lookup per attempt; the
        gain is that no future contributor can add a new send path
        and accidentally skip the guard.
        """
        self._enforce_readonly(method)
        headers = self._headers()
        if json is not None:
            headers["Content-Type"] = "application/json"
        response = self._session.request(
            method.upper(),
            url,
            headers=headers,
            json=json,
            timeout=timeout if timeout is not None else self._timeout,
            verify=self._verify_tls,
            # SEC-03a: never let `requests` silently follow a redirect.
            # If we followed, the bearer token in `headers` would be
            # replayed against whatever `Location:` pointed at. An
            # attacker who can write a 3xx response from the destination
            # could then exfiltrate the token to an external host.
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            # 3xx responses are surfaced as a transport-level refusal,
            # not as a successful body. The CLI translates this into
            # a hint about the destination URL or an explicit operator
            # override (see SEC-03b for a one-hop same-host helper).
            redirect_url = response.headers.get("Location", "")
            msg = (
                f"{method.upper()} {url} -> HTTP {response.status_code} "
                f"redirect to {redirect_url!r}; refusing to follow so the "
                "Authorization token cannot leak across hosts"
            )
            raise SnapshotTransportError(
                msg,
                base_url=self._base_url,
                redirect_url=redirect_url or None,
            )
        return response

    def _follow_one_safe_hop(self, response: requests.Response) -> str:
        """Return the ``Location`` URL of a 3xx, only when the host is unchanged.

                SEC-03b. SEC-03a refused every 3xx at the leaf send. The
                absolute "never follow" is the safe default, but some
                legitimate flows (NetBox can issue a 301 for a trailing-slash
                canonicalisation, for example) need exactly one same-host
                redirect to be allowed. This helper is the opt-in:

                * Caller catches :class:`SnapshotTransportError` from a GET,
                  notices ``exc.redirect_url`` is non-None, and asks this
                  helper whether the hop is safe.
                * The helper returns the URL only when ``(host, port)`` match
                  the client's ``base_url``. Anything else is a refusal.

        We do NOT replay the request inside the helper, the caller
                re-issues if and only if the helper returned. Any future
                edit that pushes the replay into this method MUST drop the
                Authorization header before sending if the cross-host check
                ever fails, otherwise a leaked token is the cost. As the
                current implementation simply raises on a cross-host hit,
                the header never reaches the wrong host.
        """

        location = response.headers.get("Location", "")
        if not location:
            raise SnapshotTransportError(
                "3xx response carried no Location header; refusing to redirect",
                base_url=self._base_url,
            )

        base_host, base_port = _host_port(self._base_url)
        target_host, target_port = _host_port(location)
        if (base_host, base_port) != (target_host, target_port):
            raise SnapshotTransportError(
                f"refusing cross-host redirect from "
                f"{base_host}:{base_port} to {target_host}:{target_port}",
                base_url=self._base_url,
                redirect_url=location,
            )
        return location

    def _translate_transport_exc(self, exc: Exception) -> SnapshotConnectivityError:
        """Convert a low-level ``requests`` failure into our domain exception.

        ARCH-07b. The ``requests`` exception hierarchy is wide and leaks
        through to call sites that have no business knowing about it.
        Inside the HTTP package we still catch the bare types so the
        retry envelope can decide on each, but the moment we hand the
        failure back to a caller (after retries are exhausted) we
        re-raise as :class:`SnapshotConnectivityError` so the CLI and
        the graph builder only ever import from :mod:`nbsnap.http`.

        The ``reason`` discriminator lets the CLI render a precise
        hint per failure category, see
        :class:`SnapshotConnectivityError` for the contract.
        """

        # The order matters: ``SSLError`` inherits from
        # ``ConnectionError`` in the requests hierarchy. Checking
        # SSLError first means we report "tls" for the more specific
        # cert/handshake failure rather than the generic
        # "connection" label.
        if isinstance(exc, requests.exceptions.SSLError):
            reason: str = "tls"
        elif isinstance(exc, requests.exceptions.Timeout):
            reason = "timeout"
        else:
            reason = "connection"
        return SnapshotConnectivityError(
            f"{type(exc).__name__}: {exc}",
            reason=reason,  # type: ignore[arg-type]
            base_url=self._base_url,
        )

    def _backoff_for(self, attempt_index: int) -> float:
        """Return the backoff delay for the Nth retry, clamped to the schedule."""
        if not self._backoff:
            return 0.0
        idx = min(attempt_index, len(self._backoff) - 1)
        return self._backoff[idx]

    def _request(self, method: str, path: str, *, json: Any = None) -> Any:
        """Run a request with the friction-10 retry envelope.

        Retry rules, mirroring `nb2kea`:

        * Retry on a connection-level failure (no status), HTTP 429,
          or HTTP 5xx.
        * Honour `Retry-After` when the server sets it (integer or
          HTTP-date forms).
        * Exponential backoff `(0.5, 1.5, 3.0)`, reuse the last entry
          if `max_retries` exceeds the schedule length.
        * Cap at `max_retries` total retries. The original attempt
          plus N retries means at most `1 + N` calls.
        * No retry on 4xx other than 429.
        * No retry on 3xx, the leaf `_send` raises
          `SnapshotTransportError` immediately (SEC-03a). A redirect is
          a routing decision the operator should make, not a retryable
          transport hiccup.

        The source-readonly guard lives on `_send`, so a write against
        a source-bound client raises before any retry budget is touched.
        See SEC-02a.
        """
        url = self._build_url(path)

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._send(method, url, json=json)
            except (
                requests.ConnectionError,
                requests.Timeout,
            ) as exc:
                last_exc = exc
                if attempt >= self._max_retries:
                    break
                wait = self._backoff_for(attempt)
                logger.warning(
                    "%s %s connection error %s, retrying in %.1fs",
                    method.upper(),
                    url,
                    type(exc).__name__,
                    wait,
                )
                time.sleep(wait)
                continue

            if response.status_code == 429:
                if attempt >= self._max_retries:
                    raise NetboxHTTPError(method.upper(), url, 429, response.text)
                parsed = _parse_retry_after(response.headers.get("Retry-After"))
                wait = parsed if parsed is not None else self._backoff_for(attempt)
                logger.warning("%s %s -> 429, retrying in %.1fs", method.upper(), url, wait)
                time.sleep(wait)
                continue

            if 500 <= response.status_code < 600:
                if attempt >= self._max_retries:
                    raise NetboxHTTPError(method.upper(), url, response.status_code, response.text)
                wait = self._backoff_for(attempt)
                logger.warning(
                    "%s %s -> %d, retrying in %.1fs",
                    method.upper(),
                    url,
                    response.status_code,
                    wait,
                )
                time.sleep(wait)
                continue

            if response.status_code in (401, 403):
                # ARCH-07b: route auth-flavoured failures through the
                # dedicated SnapshotAuthError so the CLI can distinguish
                # "renew your token" from "ask an admin for permission"
                # without sniffing message text. The generic 4xx branch
                # below still catches every other client error as the
                # legacy NetboxHTTPError; ARCH-07b deliberately does not
                # widen the auth handling beyond 401 and 403.
                raise SnapshotAuthError(
                    f"{method.upper()} {url} -> HTTP {response.status_code}: {response.text[:200]}",
                    status=response.status_code,
                    base_url=self._base_url,
                )

            if response.status_code >= 400:
                raise NetboxHTTPError(method.upper(), url, response.status_code, response.text)
            if response.status_code == 204 or not response.content:
                return None
            return response.json()

        # Exhausted retries with a connection-level exception. ARCH-07b
        # translates the bare requests error into the nbsnap-domain
        # SnapshotConnectivityError so callers outside the http package
        # never have to import from `requests`.
        assert last_exc is not None
        raise self._translate_transport_exc(last_exc) from last_exc

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

    def _append_limit(self, path: str, limit: int) -> str:
        """Add `limit=<n>` to the query string, idempotent on existing limit.

        Handles two input shapes:
          * a relative API path (`dcim/devices/`), wrapped through
            `_build_url` to an absolute URL first.
          * an already-absolute URL (the shrink loop hands the same
            URL back in for the smaller limit), which we accept
            without re-wrapping.
        """
        absolute = path if path.startswith("http") else self._build_url(path)
        split = urlsplit(absolute)
        existing = split.query
        # Strip any prior `limit=`. We rebuild instead of `replace`
        # because `replace` would happily mangle `?limit=500&other=...`.
        kept = "&".join(
            piece for piece in existing.split("&") if piece and not piece.startswith("limit=")
        )
        query = urlencode({"limit": limit})
        if kept:
            query = f"{kept}&{query}"
        return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))

    def _shrink_step(self, current: int) -> int | None:
        """Return the next-smaller page size or None at the floor."""
        for ladder_value in SHRINK_LADDER:
            if ladder_value < current:
                return ladder_value
        return None

    def get_all(self, path: str) -> Iterator[dict[str, Any]]:
        """Iterate every page of a NetBox list endpoint.

        Follows the `next` link returned by NetBox (friction-10 M2
        forbids `?limit=0`; we walk pages defensively even when the
        first response looks complete).

        On a connection timeout, halves the page size and retries
        until either success or the SHRINK_LADDER floor. The shrunk
        size is cached on the instance so the rest of the run uses
        the discovered upper bound.

        The source-readonly guard runs inside ``_send`` (SEC-02a), so
        the GET we issue here is checked on the same path as any other
        verb. No explicit pre-check is needed.
        """
        url: str | None = self._append_limit(path, self._page_size)
        expected_total: int | None = None
        running_total = 0

        while url is not None:
            payload = self._get_with_shrink(url)
            if expected_total is None:
                expected_total = payload.get("count")

            for row in payload.get("results") or []:
                running_total += 1
                yield row

            next_url = payload.get("next")
            url = next_url if isinstance(next_url, str) else None

        if expected_total is not None and running_total != expected_total:
            logger.warning(
                "pagination count mismatch on %s: server reported %d, yielded %d",
                path,
                expected_total,
                running_total,
            )

    def _get_with_shrink(self, url: str) -> dict[str, Any]:
        """Single page GET with the FEAT-01f shrink ladder applied."""
        current_limit = self._page_size
        while True:
            try:
                response = self._send("GET", url)
            except (requests.ConnectionError, requests.Timeout) as exc:
                next_limit = self._shrink_step(current_limit)
                if next_limit is None:
                    raise
                logger.warning(
                    "timeout at limit=%d on %s, shrinking to limit=%d (%s)",
                    current_limit,
                    url,
                    next_limit,
                    type(exc).__name__,
                )
                # Each shrink counts as a retry against the budget.
                # The retry envelope on `_request` does not apply
                # here because pagination has its own loop.
                url = self._append_limit(url, next_limit)
                current_limit = next_limit
                self._page_size = next_limit  # cache for the rest of the run
                continue

            if response.status_code >= 400:
                raise NetboxHTTPError("GET", url, response.status_code, response.text)
            data = response.json()
            assert isinstance(data, dict)
            return data

    def get_all_with_progress(self, path: str) -> Iterator[tuple[int, int, dict[str, Any]]]:
        """Variant of `get_all` that yields `(index, total, row)` triples.

        Lets callers show "page 12 of 47" style progress without doing
        a separate count pass against the server. The total is `None`
        until the first page arrives, so the first row reports the
        total alongside it.
        """
        index = 0
        total = 0
        first_page = True
        url: str | None = self._append_limit(path, self._page_size)

        while url is not None:
            payload = self._get_with_shrink(url)
            if first_page:
                total = int(payload.get("count") or 0)
                first_page = False
            for row in payload.get("results") or []:
                index += 1
                yield index, total, row
            next_url = payload.get("next")
            url = next_url if isinstance(next_url, str) else None
