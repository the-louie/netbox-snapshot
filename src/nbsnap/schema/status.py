"""NetBox version + plugin fetcher (FEAT-04a/b).

Used by the manifest writer (FEAT-15) and the import-side version
skew check (FEAT-25 `--max-version-skew`).

The status payload from NetBox 4.x carries:

* `netbox-version`, the dotted version string.
* `python-version`, informational, surfaced in the manifest.
* `installed-apps`, a list of `name@version` entries.
* `plugins`, a dict keyed by plugin name with the version string.

We tolerate missing fields with `.get` because older NetBox lines
shipped slightly different keys; the manifest writer surfaces
"unknown" rather than a crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nbsnap.http.client import NetboxHTTP


@dataclass(frozen=True)
class PluginInfo:
    """One installed NetBox plugin."""

    name: str
    version: str


class VersionSkew(Enum):
    """How far two NetBox versions are apart.

    `NONE < PATCH < MINOR < MAJOR`. The comparison operators below
    keep the natural reading "this skew is at most that big".
    """

    NONE = 0
    PATCH = 1
    MINOR = 2
    MAJOR = 3

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, VersionSkew):
            return NotImplemented
        return self.value >= other.value

    def __le__(self, other: object) -> bool:
        if not isinstance(other, VersionSkew):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, VersionSkew):
            return NotImplemented
        return self.value > other.value

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, VersionSkew):
            return NotImplemented
        return self.value < other.value

    def allowed_by(self, tolerance: VersionSkew) -> bool:
        """True iff this skew bucket is at or below the tolerance bucket.

        Used by `--max-version-skew` in FEAT-25. The tolerance
        `MINOR` accepts NONE, PATCH, and MINOR but rejects MAJOR.
        """
        return self.value <= tolerance.value


def parse_version(s: str) -> tuple[int, int, int]:
    """Parse `MAJOR.MINOR.PATCH` (with optional pre-release tail)."""
    head = s.split("-", 1)[0]
    parts = head.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        major, minor, patch = (int(p) for p in parts[:3])
    except ValueError as exc:
        msg = f"unparseable version {s!r}"
        raise ValueError(msg) from exc
    return major, minor, patch


@dataclass(frozen=True)
class Status:
    """Snapshot of `/api/status/` plus enumerated plugins."""

    netbox_version: str
    python_version: str
    installed_apps: list[str] = field(default_factory=list)
    plugins: list[PluginInfo] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def fetch(cls, http: NetboxHTTP) -> Status:
        """Fetch `/api/status/` and `/api/plugins/`, build the dataclass.

        NetBox is inconsistent about hyphen vs underscore in the
        status payload across 3.x / 4.x / 4.1+. We accept both
        spellings of every field, falling back to "unknown" only
        when neither variant is present.
        """

        status = http.get_one("status/") or {}
        plugins_raw = http.get_one("plugins/") or {}

        netbox_version = status.get("netbox-version") or status.get("netbox_version") or "unknown"
        python_version = status.get("python-version") or status.get("python_version") or "unknown"
        installed_apps_raw = status.get("installed-apps") or status.get("installed_apps") or []
        # NetBox 4.1+ returns installed_apps as a dict of
        # {name: version}; older versions return a flat list of
        # "name@version" strings. Accept both shapes.
        if isinstance(installed_apps_raw, dict):
            installed_apps = [f"{name}@{version}" for name, version in installed_apps_raw.items()]
        else:
            installed_apps = [str(x) for x in installed_apps_raw]

        return cls(
            netbox_version=str(netbox_version),
            python_version=str(python_version),
            installed_apps=installed_apps,
            plugins=_parse_plugins(plugins_raw),
        )

    # ------------------------------------------------------------------
    # Skew calculation (FEAT-04b)
    # ------------------------------------------------------------------
    def version_skew(self, other: Status) -> VersionSkew:
        """Return the enum bucket comparing this version to `other`."""
        a = parse_version(self.netbox_version)
        b = parse_version(other.netbox_version)
        if a[0] != b[0]:
            return VersionSkew.MAJOR
        if a[1] != b[1]:
            return VersionSkew.MINOR
        if a[2] != b[2]:
            return VersionSkew.PATCH
        return VersionSkew.NONE

    def skew_allowed_by(self, other: Status, tolerance: VersionSkew) -> bool:
        """Convenience: compute the skew vs `other` and compare to tolerance."""
        return self.version_skew(other).allowed_by(tolerance)


def _parse_plugins(raw: dict[str, object] | list[object] | object) -> list[PluginInfo]:
    """Build a `PluginInfo` list from the NetBox `/plugins/` payload.

    NetBox has shipped this endpoint in two shapes over time. Most
    recent versions return a list of `{"name": ..., "version": ...}`
    dicts; older returned a dict keyed by plugin name. We accept
    either to keep the fetcher robust.
    """
    plugins: list[PluginInfo] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                plugins.append(
                    PluginInfo(
                        name=str(entry.get("name") or "unknown"),
                        version=str(entry.get("version") or "unknown"),
                    )
                )
        return plugins
    if isinstance(raw, dict):
        for name, info in raw.items():
            version = str(info.get("version") or "unknown") if isinstance(info, dict) else str(info)
            plugins.append(PluginInfo(name=str(name), version=version))
    return plugins
