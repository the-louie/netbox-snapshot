"""Two-phase import engine. Underscore suffix sidesteps the reserved word.

ARCH-11b re-exports the import side's public entry points so a
caller embedding nbsnap from another script can write::

    from nbsnap.import_ import run_import, ResolveContext

without having to dig into ``nbsnap.import_.driver`` and
``nbsnap.import_.resolve_context``.
"""

from nbsnap.import_.driver import run_import
from nbsnap.import_.resolve_context import ResolveContext

__all__ = ["ResolveContext", "run_import"]
