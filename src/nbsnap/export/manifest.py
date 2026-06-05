"""Compatibility shim for the manifest dataclass and ``PerfTimer``.

ARCH-01b moved :class:`Manifest` (and ``MANIFEST_FILENAME``, plus
the SEC-04a derivatives :func:`compute_source_url_hash` and
``SOURCE_URL_HASH_LENGTH``) to :mod:`nbsnap.snapshot.manifest`.

This module remains during the ARCH-01e/f migration window so any
caller that still imports from ``nbsnap.export.manifest`` keeps
working. The re-exports go away in ARCH-01f once every consumer is
migrated.

:class:`PerfTimer` is **not** part of the snapshot contract, it is
export-side instrumentation only. It stays in this module.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from nbsnap.snapshot.manifest import (
    MANIFEST_FILENAME,
    SOURCE_URL_HASH_LENGTH,
    Manifest,
    compute_source_url_hash,
)


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


__all__ = [
    "MANIFEST_FILENAME",
    "SOURCE_URL_HASH_LENGTH",
    "Manifest",
    "PerfTimer",
    "compute_source_url_hash",
]
