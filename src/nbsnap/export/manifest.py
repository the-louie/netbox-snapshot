"""Snapshot manifest dataclass (FEAT-15a) and PerfTimer (FEAT-15b).

The manifest is the single file at the top of the snapshot that
tells the importer (and the operator) what the snapshot contains.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "manifest.json"


@dataclass
class Manifest:
    """Top-level snapshot description."""

    version: int = 1
    source_url: str = ""
    netbox_version: str = "unknown"
    nbsnap_version: str = "0.0.1"
    created_at: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    perf: dict[str, float] = field(default_factory=dict)
    deferred_edges: list[dict[str, Any]] = field(default_factory=list)

    def write(self, path: Path) -> None:
        """Write the manifest as canonical JSON."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(asdict(self), sort_keys=True, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> Manifest:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)


class PerfTimer:
    """Lightweight wall-clock timer for the manifest's perf section.

    Use as a context manager. The accumulated seconds land in the
    dict the timer was given so the caller does not have to manage
    the bookkeeping.
    """

    def __init__(self, sink: dict[str, float]) -> None:
        self._sink = sink

    @contextmanager
    def timer(self, label: str):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        try:
            yield
        finally:
            self._sink[label] = self._sink.get(label, 0.0) + time.perf_counter() - start
