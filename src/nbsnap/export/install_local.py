"""Install-local classifier (FEAT-13a) and flag-file writer (FEAT-13c).

Network-only scope (CLAUDE.md banner) narrows the install-local
rule set to one comparison: an `IPAddress.dns_name` that matches
the source NetBox's own hostname must not flow to the destination.

The flag-file writer records every excluded row so the operator
can audit what the snapshot omitted, this is what `flags.jsonl`
in the snapshot layout (RES-03) carries.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Flag:
    """One install-local exclusion, persisted to flags.jsonl."""

    content_type: str
    natural_key: tuple[Any, ...]
    field: str
    reason: str


def is_install_local(
    content_type: str, record: Mapping[str, Any], source_url: str
) -> Flag | None:
    """Return a Flag when the record is install-local, else None.

    Network-only scope per CLAUDE.md: the only install-local check
    is `IPAddress.dns_name` equality with the source NetBox host.
    Tenancy, Webhooks, and Config Contexts are out of scope so the
    classifier does not look at them.
    """
    if content_type != "ipam.ipaddress":
        return None
    dns_name = record.get("dns_name")
    if not isinstance(dns_name, str) or not dns_name:
        return None
    host = urlsplit(source_url).hostname
    if host and dns_name.lower() == host.lower():
        # NK reconstruction lives in resolver, callers feed us the
        # natural key when they have it; here we surface a placeholder.
        return Flag(
            content_type=content_type,
            natural_key=tuple(record.get("address") or ""),
            field="dns_name",
            reason=f"matches source host {host}",
        )
    return None


class FlagWriter:
    """Append-only JSONL writer for the flag log."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate on construction so a re-export starts clean.
        self._path.write_text("", encoding="utf-8")

    def write(self, flag: Flag) -> None:
        payload = {
            "content_type": flag.content_type,
            "natural_key": list(flag.natural_key),
            "field": flag.field,
            "reason": flag.reason,
        }
        with self._path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, sort_keys=True) + "\n")
