"""Export-side performance timer (post ARCH-01f).

ARCH-01b moved :class:`Manifest`, ``MANIFEST_FILENAME``,
``SOURCE_URL_HASH_LENGTH``, and :func:`compute_source_url_hash` to
:mod:`nbsnap.snapshot.manifest`. ARCH-01f then dropped the
back-compat re-exports from this module. Import the contract
symbols from :mod:`nbsnap.snapshot` directly; this module retains
only :class:`PerfTimer`, which is export-side instrumentation, not
part of the snapshot contract.
"""

from __future__ import annotations

import time
from contextlib import contextmanager


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


__all__ = ["PerfTimer"]
