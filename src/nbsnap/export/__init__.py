"""Per-endpoint extractors and the snapshot writer (programmatic API).

ARCH-11a re-exports the export side's public entry points so a
caller embedding nbsnap inside another script can simply::

    from nbsnap.export import run_export, Manifest

without knowing which submodule the symbols actually live in.

* :func:`run_export` is the one-call driver, equivalent to invoking
  ``nbsnap export`` on the command line.
* :class:`Manifest` is re-exported from :mod:`nbsnap.snapshot` (its
  canonical home post-ARCH-01b) so the snapshot's manifest is the
  same type whether the caller reaches it through ``export`` or
  ``snapshot``.
"""

from nbsnap.export.driver import run_export
from nbsnap.snapshot.manifest import Manifest

__all__ = ["Manifest", "run_export"]
