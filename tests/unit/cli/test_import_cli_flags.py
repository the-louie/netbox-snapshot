"""ARCH-10c: ``import_cli`` consumes the shared flag builders.

Locks the canonical flag names so a future drift between subcommands
trips here. The existing import-flow tests cover behaviour; this
file covers the *shape*.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from nbsnap.import_cli import add_import_args


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_import_args(parser)
    return parser


def test_import_cli_exposes_no_verify_tls() -> None:
    ns = _parser().parse_args(["--in", "/tmp/snap", "--no-verify-tls"])
    assert ns.no_verify_tls is True


def test_import_cli_exposes_audit_out_and_fsync() -> None:
    ns = _parser().parse_args(["--in", "/tmp/snap", "--audit-out", "/tmp/a.jsonl", "--audit-fsync"])
    assert ns.audit_out == Path("/tmp/a.jsonl")
    assert ns.audit_fsync is True


def test_import_cli_audit_out_default_is_none() -> None:
    ns = _parser().parse_args(["--in", "/tmp/snap"])
    assert ns.audit_out is None
    assert ns.audit_fsync is False
