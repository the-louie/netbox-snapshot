"""Snapshot directory layout (ARCH-01c).

This module owns the **on-disk file layout** of a snapshot, namely
which content type lives at which path relative to the snapshot root.
Before ARCH-01c, ``CONTENT_TYPE_FILES`` and :func:`relative_path`
lived in :mod:`nbsnap.export.writer`, and three importers reached
into ``export/`` for them. Moving the map and the helper into the
:mod:`nbsnap.snapshot` package makes the boundary symmetric.

Silent fallback (ARCH-08a follow-up)
-----------------------------------
For now :func:`relative_path` falls back to a derived path when the
content type is missing from the map (the original behaviour). The
fallback is dangerous because a typo on the import side would
silently write into a brand-new directory rather than fail loudly;
ARCH-08a will replace it with :class:`UnknownContentTypeError`.
That is a separate sub-ticket, deliberately not bundled here, so
this move keeps the behaviour identical and the only diff is
location.
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


def relative_path(content_type: str) -> str:
    """Return the snapshot-relative path for a content type.

    See the module docstring for the planned ARCH-08a hardening that
    will remove the silent fallback.
    """

    return CONTENT_TYPE_FILES.get(
        content_type, f"{content_type.replace('.', '/')}.jsonl"
    )


__all__ = ["CONTENT_TYPE_FILES", "relative_path"]
