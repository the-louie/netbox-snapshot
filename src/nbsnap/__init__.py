"""nbsnap, portable NetBox snapshot tool, top-level package.

ARCH-11c lifts the most useful entry points to the top of the
package so an embedding script can do::

    import nbsnap
    manifest = nbsnap.run_export(http, out_dir)
    nbsnap.run_import(http, snapshot_dir)

without knowing which subpackage owns each callable.

The four re-exports are intentionally narrow. Anything
internal that a caller might reach for (the planner, the
content-type cache, the natural-key registry) stays under its
subpackage to keep the top-level surface honest.
"""

from nbsnap.export import run_export
from nbsnap.import_ import run_import
from nbsnap.snapshot import Manifest

__version__ = "0.0.1"

__all__ = ["Manifest", "__version__", "run_export", "run_import"]
