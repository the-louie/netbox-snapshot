"""Shared CLI primitives for nbsnap's subcommands (ARCH-10).

This package owns flags that every subcommand needs, plus the
canonical defaults and help text. Before ARCH-10 each subcommand
declared its own ``--no-verify-tls``, scope, and audit flags,
which led to drift (``--only`` vs. ``--content-types``, different
help wording, inconsistent default behaviour).

ARCH-10a lands the flag builders here. ARCH-10b..d migrate each
subcommand to call them.

History
-------
ARCH-10a turned the previously module-level ``src/nbsnap/cli.py``
into this package; the entry-point dispatcher landed at
:mod:`nbsnap.cli.main` and is re-exported here so existing
imports (``from nbsnap.cli import TICKETS, main, _build_parser``)
keep working without a test-suite churn.
"""

from nbsnap.cli.main import TICKETS, _build_parser, main

__all__ = ["TICKETS", "_build_parser", "main"]
