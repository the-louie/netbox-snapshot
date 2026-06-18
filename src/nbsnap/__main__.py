"""Entry point for `python -m nbsnap`.

The console script wired by ``pyproject.toml`` (``nbsnap =
"nbsnap.cli:main"``) already covers the installed-CLI path. This
module exists so the package is also executable via the
``python -m nbsnap`` invocation, which the integration tests use
to subprocess-call the CLI without relying on the entry-point
script being on ``$PATH``.
"""

from __future__ import annotations

import sys

from nbsnap.cli import main

if __name__ == "__main__":
    sys.exit(main())
