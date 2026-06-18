"""ARCH-10d: ``plan_cli`` and ``reset_cli`` use the shared flag builders.

Locks the canonical names so a future drift between subcommands
trips here first.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from nbsnap.plan_cli import add_plan_args
from nbsnap.reset_cli import add_reset_args


def _parser(builder) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    builder(parser)
    return parser


def test_plan_cli_uses_content_types_canonical() -> None:
    ns = _parser(add_plan_args).parse_args(["--no-verify-tls", "--content-types", "dcim.site"])
    assert ns.no_verify_tls is True
    assert ns.content_types == "dcim.site"


def test_reset_cli_uses_content_types_and_audit_out() -> None:
    ns = _parser(add_reset_args).parse_args(
        [
            "--no-verify-tls",
            "--content-types",
            "dcim.site,dcim.device",
            "--audit-out",
            "/tmp/reset-audit.jsonl",
        ]
    )
    assert ns.no_verify_tls is True
    assert ns.content_types == "dcim.site,dcim.device"
    assert ns.audit_out == Path("/tmp/reset-audit.jsonl")


def test_reset_cli_only_alias_still_works() -> None:
    """The legacy ``--only`` spelling lands in the same ``content_types`` dest."""

    ns = _parser(add_reset_args).parse_args(["--only", "dcim.site"])
    assert ns.content_types == "dcim.site"
