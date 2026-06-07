"""Shared argparse flag builders for nbsnap subcommands (ARCH-10a).

Three families of flags appear on more than one subcommand:

* :func:`add_tls_flags` adds ``--no-verify-tls``. Disabling TLS
  verification is necessary against the self-signed local source,
  but a footgun against the production destination, so the help
  text spells the trade-off out.
* :func:`add_scope_flags` adds the canonical scope flag
  ``--content-types`` plus the ``--only`` deprecation alias (which
  ARCH-10e formalises with a deprecation warning).
* :func:`add_audit_flags` adds ``--audit-out`` and ``--audit-fsync``,
  the two flags that govern where and how durably the audit JSONL
  is written.

The builders only attach flags; they never read environment
variables or resolve defaults that depend on other args. The
subcommand main functions do that, calling the builders for the
flag *names* and then deriving the *values* themselves. Keeping
the builders pure makes them easy to test in isolation.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def add_tls_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the ``--no-verify-tls`` flag.

    Used by every subcommand that opens an HTTP client. The
    canonical wording calls out *when* an operator should reach for
    it (self-signed source) so the flag is not adopted as a
    default-on convenience.
    """

    parser.add_argument(
        "--no-verify-tls",
        action="store_true",
        help=(
            "disable TLS certificate verification. Required for the "
            "self-signed local source NetBox; keep verification on "
            "for production destinations."
        ),
    )


def add_scope_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the canonical scope flag and the legacy alias.

    ``--content-types`` is the canonical name. ``--only`` is kept
    as an alias for backwards compatibility with existing scripts;
    ARCH-10e turns it into a deprecation warning. Both write to the
    same dest, ``content_types``, so the subcommand reads one place.
    """

    parser.add_argument(
        "--content-types",
        "--only",
        dest="content_types",
        default=None,
        help=(
            "comma-separated list of NetBox content types to operate on "
            "(e.g. 'dcim.site,dcim.device'). When omitted, the subcommand "
            "uses its renderer-minimum default scope. The ``--only`` "
            "spelling is a deprecated alias kept for older scripts."
        ),
    )


def add_audit_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the audit-output flags shared across import and reset.

    ``--audit-out`` redirects the per-row audit JSONL. ``--audit-fsync``
    forces an fsync after every flush; an operator running on a
    spinning-disk volume can pay the latency in exchange for
    stronger durability guarantees.
    """

    parser.add_argument(
        "--audit-out",
        type=Path,
        default=None,
        help=(
            "write the per-row audit JSONL to this path "
            "(default: <snapshot_dir>/audit.jsonl)."
        ),
    )
    parser.add_argument(
        "--audit-fsync",
        action="store_true",
        help=(
            "fsync the audit JSONL after every flush. Trades latency "
            "for stronger crash-survival guarantees on spinning disks."
        ),
    )


__all__ = ["add_audit_flags", "add_scope_flags", "add_tls_flags"]
