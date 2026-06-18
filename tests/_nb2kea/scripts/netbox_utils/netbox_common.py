"""
Shared client and inventory constants for the NetBox population and renderer
scripts.

This module exists so the eight scripts in this directory share a single
implementation of the HTTP client, the API token plumbing, and the
inventory tables. When a dist is added or a table is moved, this is the
only file the operator needs to touch.

The HTTP client wraps `curl` rather than the `requests` library, which
keeps the scripts free of third party dependencies beyond `jinja2`. The
`--resolve` flag bypasses DNS, which mirrors the operator's existing
inventory bash script and avoids a runtime dependency on a working
resolver.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# .env auto load
#
# Operator credentials and the optional NB_URL override live in a .env file
# at the project root. Sourcing the file before every invocation is easy to
# forget, so the module walks up from the current working directory and
# merges any .env it finds into os.environ. Explicit environment values
# win over the file, the .env load only fills in keys that are not already
# set.
# ---------------------------------------------------------------------------

def _load_dotenv_if_present(start: str | None = None) -> str | None:
    """
    Search for a `.env` file starting at `start` (default CWD) and
    walking up to the filesystem root. The first match is parsed as
    `KEY=VALUE` lines, blank lines and lines starting with `#` are
    skipped, and each key is added to `os.environ` only when the key
    is not already set so an explicit environment value always wins.

    Returns the path that was loaded, or None when no `.env` was
    found. The return value is mainly useful for tests, the
    production path discards it.

    The format is intentionally minimal, no quoting, no inline
    comments, no variable substitution. The file is the team's
    convention for stashing `NB_TOKEN` and the optional `NB_URL`
    override, anything richer should go in a real config file.
    """
    here = os.path.abspath(start or os.getcwd())
    while True:
        candidate = os.path.join(here, ".env")
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, value = line.split("=", 1)
                        key = key.strip()
                        if key and key not in os.environ:
                            os.environ[key] = value.strip()
            except OSError:
                # An unreadable .env should not break the script,
                # the operator can still export the values manually.
                return None
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


_load_dotenv_if_present()


# ---------------------------------------------------------------------------
# Network endpoint
#
# The default targets the production NetBox via a curl `--resolve` pin so
# the script does not depend on a working resolver. An operator running
# against a different NetBox (a container lab, a staging instance, the
# Docker host bridge) sets `NB_URL` and the client trusts DNS instead.
# ---------------------------------------------------------------------------

NETBOX_HOST = "netbox.infra.glitched.se"
NETBOX_IP = "92.33.56.43"
NETBOX_PORT = 443
API_BASE = f"https://{NETBOX_HOST}/api"

# A bounded request lifetime keeps every call recoverable. NetBox under load
# returns within a few seconds, thirty seconds is a generous ceiling that
# still surfaces a wedged server reasonably quickly.
HTTP_TIMEOUT_SECONDS = 30

# TLS verification stays off for every supported environment, the
# production NetBox sits on a flat L2 segment behind the operator network
# and the host.docker.internal lab endpoint uses a self-signed cert.
# Leaving NETBOX_CA_BUNDLE as None preserves the curl `-k` default below.
NETBOX_CA_BUNDLE = os.environ.get("NETBOX_CA_BUNDLE")


def _parse_netbox_url(url: str) -> tuple[str, int]:
    """
    Parse `NB_URL` into a `(host, port)` pair. Accepts either a bare
    `host:port` or a full `https://host:port` form, defaults the port
    to 443 when the URL omits it. Raises RuntimeError on a malformed
    value so the operator sees a clear cause rather than a curl
    failure.

    The scheme is dropped on purpose, the client always speaks HTTPS,
    a `http://` URL would silently mismatch otherwise.
    """
    if not url:
        raise RuntimeError("NB_URL is empty")
    stripped = url.strip()
    for scheme in ("https://", "http://"):
        if stripped.startswith(scheme):
            stripped = stripped[len(scheme):]
            break
    stripped = stripped.rstrip("/")
    if "/" in stripped:
        stripped = stripped.split("/", 1)[0]
    if ":" in stripped:
        host, port_str = stripped.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError as exc:
            raise RuntimeError(
                f"NB_URL {url!r} has a non integer port {port_str!r}"
            ) from exc
    else:
        host, port = stripped, 443
    if not host:
        raise RuntimeError(f"NB_URL {url!r} has no host component")
    return host, port

# Bounded exponential backoff for transient failures (curl timeout, HTTP
# 5xx, HTTP 429). Three retries with these waits cap the total recovery
# time at five seconds. All scripts in this directory are idempotent so
# the retry is safe to apply to GET, POST, and PATCH alike, NetBox's
# response to a duplicate POST is a 4xx that the script already handles.
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.5, 3.0)

# curl --write-out marker that surfaces the HTTP status code on stderr.
# Putting it on stderr keeps the JSON body on stdout pristine for
# json.loads, %{stderr} switches subsequent format output to stderr.
_HTTP_STATUS_PREFIX = "NB_HTTP_STATUS="
_HTTP_STATUS_RE = re.compile(rf"{_HTTP_STATUS_PREFIX}(\d+)")


# ---------------------------------------------------------------------------
# Role identifiers
#
# The dist plan, per dist info, and per dist table list now live in
# NetBox. The role slugs below are the only inventory identifiers the
# scripts still hold in code, they pin the device queries to the team's
# established role names.
# ---------------------------------------------------------------------------

ROLE_DIST = "distribution_switches"
ROLE_ACCESS = "access_switch"

# Per hall VLAN base for participant VLANs. A table at <hall><N> uses VLAN
# HALL_VLAN_BASE[hall] + N. Adding a new hall (B, JF, LS) requires
# adding an entry here and rerunning the population scripts.
HALL_VLAN_BASE: dict[str, int] = {
    "D": 200,
    "C": 100,
}

# Canonical set of VLAN ids that are not participant tables, the single
# source of truth shared by every consumer that needs to skip the mgmt
# SVI, the OSPF linknets, the platform internal IRBs, and the crew IRB
# when iterating dist participant IRBs. Concretely,
#   600              mgmt SVI
#   500, 501         platform internal IRBs that some dists carry
#   1100, 1101       OSPF default and internet linknets, MX 01
#   1200, 1201       OSPF default and internet linknets, MX 02
#   199              crew IRB on D-INFRA-SW (operator named INFRA-CREW)
#
# Consumers, all import this constant rather than redefining their own,
#   * scripts/netbox2kea.py, the participant subnet collector
#     (crew Kea subnet is emitted through the kea-crew Prefix role, 199
#     must not surface as a second participant subnet).
#   * scripts/netbox_utils/netbox_fill_participant_vlan_names.py, the
#     auto naming script (the crew VLAN already has a deliberate name
#     and must not be renamed to a per table label).
#   * scripts/netbox_verify_renderable.py, the participant IRB validator
#     (199 is the crew IRB and is not table shaped, so the per table
#     checks must skip it).
#
# Deliberate exception, scripts/netbox2junos.py uses a narrower local set
# (`INFRA_IRB_VIDS`) that omits 199, because the Junos renderer DOES
# emit an IRB for the crew VID 199 as a v4-only participant style IRB.
# That set is not unified with this one, see the comment on its
# definition for why.
INFRASTRUCTURE_VIDS = frozenset({199, 500, 501, 600, 1100, 1101, 1200, 1201})


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class NetboxClient:
    """
    Wraps curl with the Glitched 2026 NetBox conventions, the host resolve
    override, the API token authentication, a bounded request timeout, and
    a retry policy for transient failures.

    The token is read from the NB_TOKEN environment variable on construction
    and sent through curl's stdin rather than the command line, which keeps
    it out of process listings on shared hosts.

    Requires curl 7.63 or later, the `%{stderr}` write-out directive that
    surfaces the HTTP status code on stderr was added in that release.
    Debian Buster, Ubuntu 20.04, and every modern operator host meet
    this floor. Older curl versions will silently report status as None
    and route every non zero exit through the transport branch of the
    retry decision, which keeps the client correct but loses the
    distinction between a 4xx (no retry) and a 5xx (retry).
    """

    def __init__(self, host: str = NETBOX_HOST, ip: str = NETBOX_IP,
                 port: int = NETBOX_PORT,
                 timeout: int = HTTP_TIMEOUT_SECONDS,
                 ca_bundle: str | None = NETBOX_CA_BUNDLE,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 retry_backoff_seconds: tuple[float, ...] =
                     DEFAULT_RETRY_BACKOFF_SECONDS,
                 sleep=time.sleep,
                 warn_stream=sys.stderr):
        token = os.environ.get("NB_TOKEN")
        if not token:
            raise RuntimeError("NB_TOKEN is not set in the environment.")

        # NB_URL overrides the hardcoded production endpoint, in that
        # mode the client trusts DNS to resolve the host and drops the
        # curl `--resolve` pin. This is the path lab and container
        # operators take to point the toolchain at host.docker.internal
        # or any other staging NetBox.
        nb_url = os.environ.get("NB_URL")
        if nb_url:
            override_host, override_port = _parse_netbox_url(nb_url)
            host = override_host
            port = override_port
            ip = ""  # empty IP signals "skip --resolve, use DNS"

        self._host = host
        self._ip = ip
        self._port = port
        self._timeout = timeout
        self._ca_bundle = ca_bundle
        self._token = token
        self._api_base = f"https://{host}:{port}/api" if port != 443 \
            else f"https://{host}/api"
        self._max_retries = max_retries
        # Defensive copy as a tuple keeps the schedule immutable from
        # the caller's side. The backoff array should be at least as
        # long as `max_retries`, the caller can shorten the wait
        # without changing the attempt count.
        self._backoff = tuple(retry_backoff_seconds)
        self._sleep = sleep
        self._warn = warn_stream

    @property
    def api_base(self) -> str:
        return self._api_base

    def _curl_once(self, method: str, url: str,
                   body: dict | None) -> tuple[int, int | None, str, str]:
        """
        Run curl exactly once, return (returncode, http_status, stdout,
        stderr). `http_status` is None when curl could not connect (no
        response was ever received), otherwise it is the integer HTTP
        status. The caller decides whether to retry based on the tuple.

        Putting the status marker on stderr (via curl's `%{stderr}`
        write-out directive) keeps the JSON body on stdout intact, so
        the existing `json.loads(stdout)` path is unchanged.
        """
        cmd = [
            "curl",
            "-sS",
            "--fail-with-body",
            "-w", f"%{{stderr}}{_HTTP_STATUS_PREFIX}%{{http_code}}\n",
            "--max-time", str(self._timeout),
            "-H", "@-",
            "-H", "Accept: application/json",
            "-X", method,
            url,
        ]
        # The `--resolve` pin is only useful when we have a known IP for
        # the host. NB_URL overrides clear self._ip and let DNS handle
        # name resolution (Docker bridge, lab dev, staging environments).
        if self._ip:
            cmd.extend(["--resolve",
                        f"{self._host}:{self._port}:{self._ip}"])
        if self._ca_bundle:
            cmd.extend(["--cacert", self._ca_bundle])
        else:
            cmd.append("-k")
        if body is not None:
            cmd.extend(["-H", "Content-Type: application/json",
                        "-d", json.dumps(body)])

        stdin_payload = f"Authorization: Bearer {self._token}\n"
        res = subprocess.run(
            cmd,
            input=stdin_payload,
            capture_output=True,
            text=True,
        )
        status: int | None = None
        match = _HTTP_STATUS_RE.search(res.stderr)
        if match:
            # curl emits 000 when no HTTP exchange happened (DNS or TCP
            # failure, timeout before headers). Treat 000 as "no status"
            # so the retry logic can route it through the transport
            # branch rather than the HTTP branch.
            parsed = int(match.group(1))
            status = parsed if parsed > 0 else None
        return res.returncode, status, res.stdout, res.stderr

    @staticmethod
    def _is_retryable(returncode: int, status: int | None) -> bool:
        """
        Decision rule for `_curl`'s retry wrapper. Returns True only on
        transient failures, the goal is to bounce through a momentary
        NetBox blip without retrying a permanent operator error.

        * curl exit 28 is the explicit `--max-time` timeout, retry.
        * HTTP 429 is rate limiting, retry.
        * HTTP 5xx is a server side failure, retry.
        * No HTTP status (the connection never reached the server),
          retry, the most likely cause is a TCP timeout or a NetBox
          restart mid request.
        * Everything else (4xx other than 429, a clean 2xx that the
          caller still considers an error) is not retryable.
        """
        if returncode == 28:
            return True
        if status is None and returncode != 0:
            return True
        if status == 429:
            return True
        if status is not None and 500 <= status < 600:
            return True
        return False

    def _curl(self, method: str, url: str,
              body: dict | None = None) -> dict | None:
        """
        Execute a single request with bounded retries. Retries are
        capped at `max_retries`, the backoff schedule is read element
        by element, the last element is reused if the caller asked for
        more retries than the schedule provides.

        Idempotency caveat, all scripts in this repository are
        idempotent so retrying a POST or PATCH is safe (a duplicate
        creation surfaces as a 4xx that fails the script, the operator
        re runs the script and the second attempt sees the resource and
        moves on). Non idempotent callers would need to opt out, no
        such caller exists today.
        """
        attempt = 0
        last_status: int | None = None
        last_returncode = 0
        last_stdout = ""
        last_stderr = ""
        while True:
            returncode, status, stdout, stderr = self._curl_once(
                method, url, body)
            last_status, last_returncode = status, returncode
            last_stdout, last_stderr = stdout, stderr

            if returncode == 0 and (status is None or 200 <= status < 400):
                # Success path. The legacy contract treats an empty body
                # as None, a non empty body is parsed as JSON.
                if not stdout.strip():
                    return None
                try:
                    return json.loads(stdout)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"non JSON response from {url}, body starts "
                        f"with: {stdout[:200]}"
                    ) from exc

            if attempt < self._max_retries and self._is_retryable(
                    returncode, status):
                # Clamp into the schedule, the last element is reused
                # when the caller asked for more retries than the
                # schedule covers.
                idx = min(attempt, len(self._backoff) - 1)
                wait = self._backoff[idx]
                reason = (f"HTTP {status}" if status is not None
                          else f"curl exit {returncode}")
                print(
                    f"  [warn] {method} {url}, {reason}, retry "
                    f"{attempt + 1}/{self._max_retries} in {wait}s",
                    file=self._warn,
                )
                self._sleep(wait)
                attempt += 1
                continue

            # Failure path. The error message names the final status
            # and attempt count so the operator can tell whether the
            # request ever reached NetBox.
            attempts_note = (f" after {attempt + 1} attempts"
                             if attempt > 0 else "")
            raise RuntimeError(
                f"curl {method} {url} failed{attempts_note} "
                f"(curl exit {last_returncode}, HTTP {last_status})\n"
                f"  stderr: {last_stderr.strip()}\n"
                f"  body: {last_stdout[:500]}"
            )

    def get_all(self, path: str) -> list[dict]:
        """
        Fetch a list endpoint with pagination. NetBox honours `?limit=0`
        on most list endpoints, the implementation still follows `next`
        defensively for installations that cap the page size.
        """
        if "limit=" not in path:
            sep = "&" if "?" in path else "?"
            path = f"{path}{sep}limit=0"
        url = f"{self._api_base}/{path}"
        results: list[dict] = []
        while url:
            data = self._curl("GET", url)
            if data is None:
                break
            results.extend(data.get("results", []))
            url = data.get("next")
        return results

    def get_one(self, path: str) -> dict | None:
        """Fetch a single endpoint without pagination."""
        return self._curl("GET", f"{self._api_base}/{path}")

    def post(self, path: str, body: dict) -> dict | None:
        return self._curl("POST", f"{self._api_base}/{path}", body)

    def patch(self, path: str, body: dict) -> dict | None:
        return self._curl("PATCH", f"{self._api_base}/{path}", body)


# ---------------------------------------------------------------------------
# Naming helpers, shared by the create and the renderer scripts.
# ---------------------------------------------------------------------------

def make_access_hostname(hall: str, table: int, slot: str) -> str:
    """
    Two digit zero padded table number, always with a slot letter. Single
    switch tables receive slot A, the convention agreed for 2026 so that
    later promotion to a two switch table adds B without renaming A.
    """
    return f"{hall}{table:02d}{slot}"


def port_description(hall: str, table: int, slot: str) -> str:
    """
    The Junos port description that the relay agent will lift verbatim
    into the Option 82 circuit id. The format matches the committed Kea
    reservations.
    """
    return f"TABLE; {hall}{table:02d}-{slot}"


# Cisco IOS hostname rules, a letter followed by letters, digits, or
# hyphens, total length 1 to 63 (IOS itself caps at 63). The strict
# shape rejects every character that could turn a NetBox device name
# into a path traversal (`/`, `..`), an IOS command injection
# (whitespace, newline, `;`, `!`), or a TFTP filename the server
# refuses (NUL, non ASCII). Validating at every site that consumes
# Device.name as either an IOS identifier or a path component keeps a
# malicious or accidentally edited NetBox name from flowing into a
# rendered config or onto the disk.
_CISCO_HOSTNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,62}$")


def assert_cisco_hostname(name: str | None, purpose: str) -> None:
    """
    Raise RuntimeError when `name` is not a legal Cisco IOS hostname.

    Used by `netbox2cisco.py` before it interpolates `dev["name"]` into
    the rendered `hostname` line, and by `access_config_filename` below
    before it builds the TFTP boot file path. The same regex applies in
    both places, the IOS hostname rules already reject every path
    traversal and command injection character a malicious or
    accidentally edited NetBox name might carry.

    The message names the offending value and the purpose so the
    operator can find the bad Device in NetBox without grepping the
    rendered output. Mirrors `assert_junos_identifier` above for the
    Junos side.
    """
    # fullmatch (rather than match) so a trailing newline cannot satisfy
    # the `$` anchor in Python's default regex mode. A name ending in
    # `\n` would otherwise pass the check and inject an extra line into
    # the rendered `hostname` directive.
    if not isinstance(name, str) or not _CISCO_HOSTNAME_RE.fullmatch(name):
        raise RuntimeError(
            f"NetBox value {name!r} used for {purpose} is not a legal "
            f"Cisco IOS hostname, expected the pattern "
            f"[A-Za-z][A-Za-z0-9-]{{0,62}}"
        )


def access_config_filename(device: dict) -> str:
    """
    The TFTP boot file name for an access switch, which is also the
    filename `netbox2cisco.py` writes for that switch's static config.

    Both renderers call this one helper so the two artefacts agree byte
    for byte, `netbox2cisco.py` writes `<name>` and `netbox2kea.py`
    emits the same `<name>` as the reservation `boot-file-name`. If the
    two ever diverged a switch would fetch a file that does not exist.

    The name is the lowercased Device name plus `.conf`, for example
    `D39A` becomes `d39a.conf`. TFTP servers and IOS treat filenames
    case sensitively, lowercasing fixes one canonical form for the
    served file so the operator does not have to guess the case.

    The Device name is validated against the Cisco IOS hostname regex
    before it is lowered into the filename. A NetBox edit that landed a
    name with `/`, `..`, whitespace, NUL, or any non ASCII character
    between the verify run and the render run would otherwise resolve
    to a path that escapes `--outdir`, the regex closes that window at
    the helper itself rather than relying on the caller to revalidate.
    """
    name = device.get("name")
    if not name:
        raise RuntimeError("access switch Device has no name in NetBox")
    assert_cisco_hostname(name, f"access switch boot file (Device {name!r})")
    return f"{name.lower()}.conf"


def participant_vlan_for_table(hall: str, table: int) -> int:
    """
    Each hall has a fixed base VLAN id, the table number is added to it.
    Unknown halls raise an explicit error rather than silently defaulting,
    which prevents a future B or LS hall from rendering with the wrong id.
    """
    try:
        return HALL_VLAN_BASE[hall] + table
    except KeyError as exc:
        raise KeyError(
            f"Unknown hall {hall!r}, add it to HALL_VLAN_BASE in netbox_common.py"
        ) from exc


def vlan_to_irb_description(vid: int) -> str:
    """
    The description used on the participant IRB, for example D39 or C7.

    Retained for the historical bootstrap script
    `netbox_create_dist_virtual_ifaces.py` that seeded participant IRB
    descriptions during the 2026 initial population. The active
    renderers source the IRB description from the NetBox VLAN name
    instead, see `vlans_by_vid` below.
    """
    for hall, base in HALL_VLAN_BASE.items():
        if base < vid <= base + 56:
            return f"{hall}{vid - base}"
    return f"VLAN{vid}"


# Junos identifier rules permit a leading letter followed by letters,
# digits, underscore, or hyphen. The renderer copies NetBox VLAN names
# verbatim into `set groups VLANS vlans <name>` and into unquoted IRB
# description tokens, so a NetBox name with a space, a dot, or a Unicode
# letter would produce a syntactically invalid `set` line that the
# operator would only notice when `commit check` failed on the device.
# Validating at the lookup site fails the run with a clear cause instead.
_JUNOS_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def assert_junos_identifier(name: str, purpose: str) -> None:
    """
    Raise RuntimeError when `name` is not a legal Junos identifier.

    The strict shape `^[A-Za-z][A-Za-z0-9_-]*$` matches the unquoted
    token positions the renderer emits (the VLAN group name and the
    unquoted IRB description form). The message names the offending
    string and the purpose so the operator can locate the bad VLAN
    object in NetBox without grepping the rendered output.

    Operators who need spaces or other characters in a description
    should keep the strict VLAN name and place the prose in a separate
    NetBox description field, or extend the renderer to emit a quoted
    Junos description string.
    """
    if not isinstance(name, str) or not _JUNOS_IDENT_RE.match(name):
        raise RuntimeError(
            f"NetBox value {name!r} used for {purpose} is not a legal "
            f"Junos identifier, expected the pattern "
            f"[A-Za-z][A-Za-z0-9_-]*"
        )


def vlans_by_vid(client: NetboxClient) -> dict[tuple[int | None, int], dict]:
    """
    Fetch every VLAN from NetBox and return a mapping keyed by
    `(site_id, vid)` where site_id is None for VLANs that are not
    scoped to a site.

    Per dist callers should use `lookup_vlan` which tries the dist's
    own site id first and falls back to the global None site. The
    layered scope lets the mgmt VLAN 600 carry a per dist name (one
    VLAN object per site) while infrastructure VLANs like the OSPF
    linknets stay as single global objects.

    Two VLAN objects with the same `(site_id, vid)` key produce a
    single entry, the second is logged as a warning so duplicates
    surface rather than silently masking each other.
    """
    vlans = client.get_all("ipam/vlans/")
    index: dict[tuple[int | None, int], dict] = {}
    for vlan in vlans:
        vid = vlan.get("vid")
        if vid is None:
            continue
        site = vlan.get("site") or {}
        site_id = site.get("id")
        key = (site_id, vid)
        if key in index:
            print(
                f"  [warn] duplicate VLAN id {vid} at site {site_id} in NetBox, "
                f"{index[key].get('name')!r} and {vlan.get('name')!r}, "
                f"using the latter",
                file=sys.stderr,
            )
        index[key] = vlan
    return index


def lookup_vlan(vlan_index: dict[tuple[int | None, int], dict],
                vid: int,
                site_id: int | None) -> dict | None:
    """
    Resolve a VLAN by vid, preferring the dist's site scope, falling
    back to the globally scoped object. Returns None when neither key
    is present.
    """
    if site_id is not None:
        scoped = vlan_index.get((site_id, vid))
        if scoped is not None:
            return scoped
    return vlan_index.get((None, vid))


def vlan_name_for(vlan_index: dict[tuple[int | None, int], dict],
                  vid: int,
                  site_id: int | None,
                  purpose: str) -> str:
    """
    Resolve a VLAN name by vid using site scope, or raise RuntimeError
    when the VLAN is missing, unnamed, or carries a name that is not a
    legal Junos identifier. `purpose` is interpolated into every error
    message so the operator can tell which IRB or interface triggered
    the failure.

    Junos identifier validation runs on every successful lookup, see
    `assert_junos_identifier` for the exact rule. Callers that need
    the looser quoted-description form should resolve the VLAN object
    directly through `lookup_vlan` rather than this helper.
    """
    vlan = lookup_vlan(vlan_index, vid, site_id)
    if vlan is None:
        raise RuntimeError(
            f"{purpose} expects VLAN id {vid} in NetBox, none found "
            f"at site {site_id} or globally"
        )
    name = vlan.get("name")
    if not name:
        raise RuntimeError(
            f"VLAN with VID {vid} resolved for {purpose} has no name "
            f"set in NetBox"
        )
    # Every NetBox VLAN name the renderer consumes ends up in a Junos
    # `set` line where the unquoted token must be a legal identifier.
    # Validating here catches the bad name at the lookup site, rather
    # than letting it flow through participants, mgmt, and OSPF call
    # sites and surfacing only at `commit check`.
    assert_junos_identifier(name, purpose)
    return name


def kea_dist_pool_ranges(client: NetboxClient) -> list[dict]:
    """
    Fetch every IP Range with role `kea-dist-mgmt` once, returned as
    a list. Callers that resolve more than one dist should fetch the
    list once and pass it into `kea_dist_pool_for_subnet` rather than
    paying for the round trip per dist.
    """
    return client.get_all("ipam/ip-ranges/?role=kea-dist-mgmt")


def kea_dist_pool_for_subnet(client: NetboxClient,
                             mgmt_v4: str,
                             ranges: list[dict] | None = None) -> str:
    """
    Resolve the DHCP pool range for a dist's mgmt /24 from a NetBox
    IP Range that carries the role `kea-dist-mgmt` and sits entirely
    within the given /24. Returns the pool formatted as
    `<start> - <end>`.

    The caller can pass a pre fetched list of ranges through `ranges`
    to avoid one network round trip per dist. When `ranges` is None
    the helper fetches the list itself for single shot callers.

    Raises RuntimeError when no matching range exists, when more than
    one starts inside the /24, or when a matching range's end address
    falls outside the /24. The end check surfaces an operator data
    error that would otherwise produce a Kea pool that spills outside
    the subnet.
    """
    net = ipaddress.ip_network(mgmt_v4)
    if ranges is None:
        ranges = client.get_all("ipam/ip-ranges/?role=kea-dist-mgmt")
    candidates: list[dict] = []
    for r in ranges:
        start_str = strip_prefix_len(r.get("start_address"))
        if not start_str:
            continue
        if ipaddress.ip_address(start_str) in net:
            candidates.append(r)
    if not candidates:
        raise RuntimeError(
            f"No IP Range with role kea-dist-mgmt starts within {mgmt_v4}"
        )
    if len(candidates) > 1:
        labels = [c.get("display") for c in candidates]
        raise RuntimeError(
            f"Multiple kea-dist-mgmt IP Ranges in {mgmt_v4}, {labels}"
        )
    chosen = candidates[0]
    start = strip_prefix_len(chosen["start_address"])
    end = strip_prefix_len(chosen["end_address"])
    if ipaddress.ip_address(end) not in net:
        raise RuntimeError(
            f"IP Range {chosen.get('display')!r} starts inside {mgmt_v4} "
            f"but ends at {end}, which is outside the subnet"
        )
    return f"{start} - {end}"


def strip_prefix_len(addr_with_plen: str | None) -> str | None:
    """
    NetBox returns IPs as `<addr>/<plen>`. Kea reservations want the bare
    address, this helper extracts it without falling over on `None`.
    """
    if not addr_with_plen:
        return None
    return addr_with_plen.split("/", 1)[0]


# ---------------------------------------------------------------------------
# Common entry point check
# ---------------------------------------------------------------------------

def require_token(stream=sys.stderr) -> bool:
    """
    Return True if NB_TOKEN is set, otherwise print a clear message and
    return False. Each script can call this at the start of main and exit
    cleanly instead of constructing a NetboxClient just to fail.
    """
    if not os.environ.get("NB_TOKEN"):
        print("Error: NB_TOKEN environment variable is not set, "
              "export your NetBox API token first.", file=stream)
        return False
    return True


def configure_logging(verbose: bool = False,
                      *,
                      level_override: int | None = None,
                      stream=sys.stderr) -> logging.Logger:
    """
    Configure the root logger with the Glitched 2026 default format.
    Each script that uses Python `logging` should call this once at the
    start of `main`, before any `log.info` call.

    The format matches the entry the TODO #4 ticket spelled out,
    `%(asctime)s %(levelname)s %(name)s, %(message)s`. The default
    level is INFO so the existing operator visible progress lines
    stay visible. `verbose=True` flips the threshold to DEBUG so
    `log.debug` calls become visible without rewiring per script.

    Returns the root logger so the caller can chain further
    customisation. Repeated calls overwrite the previous handler so
    a wrapper or test can re configure without leaking handlers.
    """
    if level_override is not None:
        level = level_override
    else:
        level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    # Clear any handler the runtime or a previous call left on the
    # root logger, otherwise repeated configuration duplicates every
    # message.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s, %(message)s",
    ))
    root.addHandler(handler)
    root.setLevel(level)
    return root


def atomic_write_text(path: str, content: str) -> None:
    """
    Write `content` to `path` atomically, the operator visible final
    path either contains the full new content or stays untouched.

    The implementation writes to `<path>.tmp` first then uses
    `os.replace` to swap it into place. `os.replace` is atomic on
    POSIX when source and destination sit on the same filesystem,
    which is always true here because the tmp file is built from the
    same dirname. A process kill mid write leaves the `.tmp` lying
    around but never a truncated file at the final path, which
    matters most for `netbox2cisco.py` whose output is fetched by an
    access switch at boot, IOS may accept a partial config without
    flagging it.

    Callers that need a different mode (binary, append, line by line
    streaming) should write their own helper, this one targets the
    "render an entire file in memory then write once" pattern that
    both renderers share.
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        fh.write(content)
    os.replace(tmp, path)


def confirm_overwrite(intended_paths: list[str],
                      overwrite: bool,
                      stdin=sys.stdin,
                      stream=sys.stderr) -> bool:
    """
    Guard the renderer against silently overwriting files in the output
    directory. Returns True when the caller may proceed with writing,
    False when it must abort.

    The decision tree:

    * No intended path already exists, return True with no output.
    * `overwrite=True`, log the count of files that will be replaced and
      return True. Operator opted in on the command line, no prompt.
    * Files exist and the caller's stdin is a TTY, print the count and
      prompt for `y/N`. Return True only on `y` or `yes`.
    * Files exist and stdin is not a TTY (CI or `< /dev/null`), refuse
      with a message that names the `--overwrite` flag. Returns False.
      Refusing on a non interactive run is the conservative choice, it
      prevents a scheduled run from quietly clobbering operator output.

    The helper does not delete or rename anything, it only decides
    whether the caller should proceed.
    """
    existing = [p for p in intended_paths if os.path.exists(p)]
    if not existing:
        return True
    if overwrite:
        print(f"Note, --overwrite is set, {len(existing)} existing file(s) "
              f"in the output directory will be replaced.", file=stream)
        return True
    if stdin.isatty():
        # Prompt is intentionally on stderr so a script that redirects
        # stdout to a file still surfaces the question to the operator.
        print(f"Warning, {len(existing)} file(s) in the output directory "
              f"would be overwritten, examples,", file=stream)
        for p in existing[:3]:
            print(f"  {p}", file=stream)
        if len(existing) > 3:
            print(f"  ... and {len(existing) - 3} more", file=stream)
        try:
            answer = input("Proceed and overwrite? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        return answer in ("y", "yes")
    print(f"Error, {len(existing)} file(s) in the output directory would "
          f"be overwritten, rerun with --overwrite to confirm.",
          file=stream)
    return False


# ---------------------------------------------------------------------------
# Kea data lookups
#
# These helpers resolve the constants `netbox2kea.py` used to hold in code.
# They depend on the data layout that `netbox_fill_kea_data.py` establishes,
# two Prefix Roles (`kea-bootstrap`, `kea-crew`) carrying both a Prefix and
# a matching IP Range, plus the existing `dns_name` field on the service
# IPAddress objects.
# ---------------------------------------------------------------------------

def kea_subnet_from_role(client: NetboxClient, role_slug: str) -> dict[str, str]:
    """
    Resolve a Kea subnet from a Prefix Role and the IP Range that carries
    the same role.

    Returns a dict with three string keys,
      subnet, the Prefix CIDR for example "92.33.43.192/26"
      router, the first usable host of the prefix, derived as network+1
      pool,   the IP Range expressed as "<start> - <end>" without prefix lengths

    Raises RuntimeError if either the Prefix or the IP Range is missing,
    which surfaces an unfilled NetBox state instead of silently emitting
    a config with empty fields.
    """
    prefixes = client.get_all(f"ipam/prefixes/?role={role_slug}")
    if not prefixes:
        raise RuntimeError(f"No prefix carries role {role_slug!r}, "
                           f"run netbox_fill_kea_data.py")
    prefix_obj = prefixes[0]
    subnet = prefix_obj["prefix"]
    # The gateway convention is the first usable host in the prefix, which
    # for an IPv4 subnet of /n is network_address + 1.
    net = ipaddress.ip_network(subnet)
    router = str(net.network_address + 1)

    ranges = client.get_all(f"ipam/ip-ranges/?role={role_slug}")
    if not ranges:
        raise RuntimeError(f"No ip-range carries role {role_slug!r}, "
                           f"run netbox_fill_kea_data.py")
    rng = ranges[0]
    start = strip_prefix_len(rng["start_address"])
    end = strip_prefix_len(rng["end_address"])
    pool = f"{start} - {end}"

    return {"subnet": subnet, "router": router, "pool": pool}


def kea_service_ips(client: NetboxClient,
                    dns_name_prefix: str,
                    family: int | None = None) -> list[str]:
    """
    Return bare addresses for every IPAddress whose dns_name starts with
    `dns_name_prefix`, sorted to keep the output stable across runs.

    The NetBox filter `dns_name__isw` matches dns_name values that start
    with the given fragment in a case insensitive way, which fits the
    ns01, ns02, dhcp01, dhcp02 naming the team already uses.

    When `family` is set to 4 or 6 the result is restricted to that
    address family, which is needed for DHCP options that accept only one
    family (Kea Option 6 takes IPv4 only, the DHCPv6 dns-servers takes
    IPv6 only).
    """
    matches = client.get_all(
        f"ipam/ip-addresses/?dns_name__isw={dns_name_prefix}"
    )
    addrs: list[str] = []
    for m in matches:
        if not m.get("address"):
            continue
        if family is not None:
            entry_family = (m.get("family") or {}).get("value")
            if entry_family != family:
                continue
        bare = strip_prefix_len(m["address"])
        if bare:
            addrs.append(bare)
    return sorted(addrs)


def kea_service_ip_exact(client: NetboxClient, dns_name: str) -> str | None:
    """
    Return the bare IPv4 for an exact dns_name match, or None when no IP
    carries that name. Useful for singleton services like TFTP.
    """
    matches = client.get_all(f"ipam/ip-addresses/?dns_name={dns_name}")
    if not matches:
        return None
    return strip_prefix_len(matches[0]["address"])


# ---------------------------------------------------------------------------
# Dist data lookups
#
# These helpers resolve `DIST_INFO` and `DIST_TABLES` by reading values that
# `netbox_fill_district_token.py` and `netbox_fill_switch_count.py` placed
# in NetBox, plus the dist's existing interface IPs and Location.
#
# The intent is that DIST_INFO and DIST_TABLES become derivable rather
# than maintained by hand. The two module level dicts remain as a stable
# enumeration of which dists exist, the helpers fill in everything else.
# ---------------------------------------------------------------------------

_TABLE_RACK_RE = re.compile(r"^[A-Z]+(\d+)$")


def _extract_table_number(rack_name: str) -> int | None:
    """
    Extract the table number from a strict `<hall><digits>` rack name.
    Returns None for any other shape, which excludes dist racks like
    `TheForge_Dist` or `EsportsCity1_Dist` from participant lookups.
    """
    match = _TABLE_RACK_RE.match(rack_name)
    return int(match.group(1)) if match else None


def dist_info_for(client: NetboxClient, dist_name: str) -> dict[str, Any]:
    """
    Resolve the per dist info dict by name.

    Looks the Device up by hostname, then delegates to
    `dist_info_from_device`. Callers that already hold the Device dict
    should use the latter directly to avoid the redundant fetch.

    Raises RuntimeError when a required value is missing, which surfaces
    a partially populated NetBox state at the point of use rather than
    silently producing wrong config.
    """
    devices = client.get_all(f"dcim/devices/?name={dist_name}")
    if not devices:
        raise RuntimeError(f"Device {dist_name!r} not found")
    return dist_info_from_device(client, devices[0])


def dist_info_from_device(client: NetboxClient,
                          dist: dict) -> dict[str, Any]:
    """
    Resolve the per dist info dict from a pre fetched Device payload.

    Returns a dict with six keys, district_token, slug, mgmt_v4,
    mgmt_gateway, loopback_octet, and subnet_id. The slug comes from
    the dist's Location.slug in NetBox, the subnet id is the team's
    Kea convention, ten times the third octet of the mgmt /24.
    """
    name = dist.get("name", "<unknown>")
    # The token comes from the custom field set during initial
    # population, the renderer keeps using it for the Junos VLAN name
    # of the mgmt SVI fallback and for diagnostic messages.
    token = (dist.get("custom_fields") or {}).get("district_token")
    if not token:
        raise RuntimeError(
            f"Device {name!r} has no district_token custom field set"
        )

    # The slug used for per dist Kea include filenames is sourced from
    # the dist's Location.slug, which NetBox auto generates from the
    # Location name. Tying the filename to NetBox state means renaming a
    # Location renames the include file, which keeps the deploy artefact
    # synchronized with NetBox as the source of truth.
    location = dist.get("location") or {}
    slug = location.get("slug")
    if not slug:
        raise RuntimeError(
            f"Device {name!r} has no Location.slug in NetBox, set a "
            f"Location on the dist and let NetBox derive its slug"
        )

    # The remaining values are read from the dist's interface IPs.
    ips = client.get_all(f"ipam/ip-addresses/?device_id={dist['id']}")
    lo_addr: str | None = None
    irb600_addr: str | None = None
    for ip in ips:
        assigned = ip.get("assigned_object") or {}
        iface_name = assigned.get("name")
        if iface_name == "lo0.0":
            lo_addr = ip["address"]
        elif iface_name == "irb.600":
            irb600_addr = ip["address"]

    if not lo_addr:
        raise RuntimeError(
            f"Device {name!r} has no IP assigned on lo0.0"
        )
    if not irb600_addr:
        raise RuntimeError(
            f"Device {name!r} has no IP assigned on irb.600"
        )

    # The loopback octet is the last byte of the /32 loopback, the
    # bootstrap script still consumes it. The mgmt /24 is the containing
    # network of the irb.600 IP, the gateway is the bare irb.600 IP. The
    # subnet id is the team's convention, ten times the third octet of
    # the mgmt /24, which encodes the sequential dist deployment order
    # and stays greppable in Kea output (10, 20, 30 ... 90).
    loopback_octet = int(lo_addr.split("/")[0].split(".")[-1])
    mgmt_v4 = str(ipaddress.ip_interface(irb600_addr).network)
    mgmt_gateway = strip_prefix_len(irb600_addr)
    mgmt_third_octet = int(mgmt_v4.split("/")[0].split(".")[2])
    subnet_id = mgmt_third_octet * 10

    return {
        "district_token": token,
        "slug":           slug,
        "mgmt_v4":        mgmt_v4,
        "mgmt_gateway":   mgmt_gateway,
        "loopback_octet": loopback_octet,
        "subnet_id":      subnet_id,
    }


def access_uplinks(cables: list[dict],
                   access_by_id: dict[int, dict],
                   ) -> dict[int, list[tuple[dict, dict]]]:
    """
    Map each access switch Device id to the list of
    `(dist_port_object, dist_device)` it is cabled to.

    `cables` is the list from `client.get_all("dcim/cables/")`,
    `access_by_id` is `{device_id: device}` for the access switches in
    scope. Each cable lands on one access switch and one dist port, but
    the termination order is not guaranteed, so both ends are checked.
    Cables that do not touch an access switch in `access_by_id` are
    ignored. A switch with two uplinks yields a two element list, the
    caller decides whether that is an error.

    This walk lived inline in `netbox2kea.py` and the verify script and
    is the single fragile mapping between the access and dist layers, so
    it lives here once and every consumer reads the same edges.
    """
    uplinks: dict[int, list[tuple[dict, dict]]] = {}
    for cable in cables:
        a_terms = cable.get("a_terminations") or []
        b_terms = cable.get("b_terminations") or []
        if not a_terms or not b_terms:
            continue
        a_obj = a_terms[0].get("object") or {}
        b_obj = b_terms[0].get("object") or {}
        a_dev = a_obj.get("device") or {}
        b_dev = b_obj.get("device") or {}
        if a_dev.get("id") in access_by_id:
            uplinks.setdefault(a_dev["id"], []).append((b_obj, b_dev))
        elif b_dev.get("id") in access_by_id:
            uplinks.setdefault(b_dev["id"], []).append((a_obj, a_dev))
    return uplinks


def dist_tables_for(client: NetboxClient,
                    dist_name: str) -> list[tuple[int, int]]:
    """
    Resolve the (table number, switch count) list that `DIST_TABLES`
    carries today by reading the dist's Location and the `switch_count`
    custom field on every participant rack inside it.

    The list is returned in natural numeric order of the rack name, which
    matches the operator's ROWS.TXT order for every existing district
    (D1, D2 .. D9, D10) and also handles the cross district number gaps
    that Tokyo Town and Tilted Blocks carry (D1..D8 then D17, D18).

    Dist racks like `TheForge_Dist` are skipped because their names do
    not match the strict `<hall><digits>` shape, even if a switch_count
    was set on one by mistake.
    """
    devices = client.get_all(f"dcim/devices/?name={dist_name}")
    if not devices:
        raise RuntimeError(f"Device {dist_name!r} not found")
    dist = devices[0]
    location_id = (dist.get("location") or {}).get("id")
    if location_id is None:
        raise RuntimeError(
            f"Device {dist_name!r} has no Location set, "
            f"the helper cannot identify which racks it serves"
        )

    racks = client.get_all(f"dcim/racks/?location_id={location_id}")
    entries: list[tuple[int, int]] = []
    for rack in racks:
        table_num = _extract_table_number(rack["name"])
        if table_num is None:
            continue
        count = (rack.get("custom_fields") or {}).get("switch_count")
        if count is None:
            continue
        entries.append((table_num, int(count)))
    entries.sort()
    return entries
