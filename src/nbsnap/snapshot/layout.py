"""Snapshot directory layout (ARCH-01c + ARCH-08a).

This module owns the **on-disk file layout** of a snapshot, namely
which content type lives at which path relative to the snapshot root.
Before ARCH-01c, ``CONTENT_TYPE_FILES`` and :func:`relative_path`
lived in :mod:`nbsnap.export.writer`, and three importers reached
into ``export/`` for them. Moving the map and the helper into the
:mod:`nbsnap.snapshot` package makes the boundary symmetric.

Hard failure on unknown content types (ARCH-08a)
------------------------------------------------
:func:`relative_path` used to fall back to a derived path
(``content_type.replace('.', '/') + '.jsonl'``) when the content
type was absent from :data:`CONTENT_TYPE_FILES`. That was
dangerous: a typo (``dcim.devic`` vs. ``dcim.device``) silently
created a fresh sibling file rather than failing where the
operator could see it. ARCH-08a replaces the fallback with
:class:`UnknownContentTypeError`. ARCH-08b extends the same check
into the importer's preflight so a snapshot whose manifest lists
unknown content types is refused before any HTTP call goes out.
"""

from __future__ import annotations

CONTENT_TYPE_FILES: dict[str, str] = {
    "dcim.site": "dcim/sites.jsonl",
    "dcim.location": "dcim/locations.jsonl",
    "dcim.rack": "dcim/racks.jsonl",
    "dcim.devicerole": "dcim/device-roles.jsonl",
    "dcim.devicetype": "dcim/device-types.jsonl",
    "dcim.manufacturer": "dcim/manufacturers.jsonl",
    "dcim.platform": "dcim/platforms.jsonl",
    "dcim.device": "dcim/devices.jsonl",
    "dcim.interface": "dcim/interfaces.jsonl",
    "dcim.frontport": "dcim/front-ports.jsonl",
    "dcim.rearport": "dcim/rear-ports.jsonl",
    "dcim.cable": "dcim/cables.jsonl",
    "ipam.role": "ipam/roles.jsonl",
    "ipam.vlan": "ipam/vlans.jsonl",
    "ipam.prefix": "ipam/prefixes.jsonl",
    "ipam.iprange": "ipam/ip-ranges.jsonl",
    "ipam.ipaddress": "ipam/ip-addresses.jsonl",
    "extras.tag": "extras/tags.jsonl",
    "extras.customfield": "extras/custom-fields.jsonl",
    "extras.customfieldchoiceset": "extras/custom-field-choice-sets.jsonl",
}


class UnknownContentTypeError(KeyError):
    """Raised when a content type has no entry in :data:`CONTENT_TYPE_FILES`.

    Carries the offending ``content_type`` string on the
    ``content_type`` attribute so a caller can surface it without
    re-parsing the message. Inherits :class:`KeyError` so any existing
    ``except KeyError`` clause (the previous silent fallback path
    surfaced a KeyError once the operator added stricter checks)
    still catches it during the migration.
    """

    def __init__(self, content_type: str) -> None:
        self.content_type = content_type
        super().__init__(
            f"unknown content type {content_type!r}; expected one of "
            f"{sorted(CONTENT_TYPE_FILES)}"
        )


def relative_path(content_type: str) -> str:
    """Return the snapshot-relative path for a content type.

    Raises :class:`UnknownContentTypeError` when ``content_type`` is
    not registered in :data:`CONTENT_TYPE_FILES`. ARCH-08a removed
    the silent fallback that previously masked typos by writing to
    a brand-new file path.
    """

    try:
        return CONTENT_TYPE_FILES[content_type]
    except KeyError as exc:
        raise UnknownContentTypeError(content_type) from exc


__all__ = ["CONTENT_TYPE_FILES", "UnknownContentTypeError", "relative_path"]
