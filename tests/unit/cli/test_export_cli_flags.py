"""ARCH-10b: export_cli uses the shared flag builders.

We do not assert the export subcommand's full namespace shape here,
the existing export tests cover that. The point is to lock the
contract: ``--no-verify-tls`` and ``--content-types`` resolve via
cli.common, so a future drift on the canonical names trips here
first.
"""

from __future__ import annotations

import argparse

from nbsnap.export_cli import add_export_args


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_export_args(parser)
    return parser


def test_export_cli_exposes_no_verify_tls() -> None:
    ns = _parser().parse_args(["--out", "/tmp/x", "--no-verify-tls"])
    assert ns.no_verify_tls is True


def test_export_cli_exposes_content_types() -> None:
    ns = _parser().parse_args(["--out", "/tmp/x", "--content-types", "dcim.site"])
    assert ns.content_types == "dcim.site"


def test_export_cli_only_is_still_accepted_as_alias() -> None:
    """ARCH-10e will turn ``--only`` into a deprecation warning; for now
    we only assert it parses to the same dest."""

    ns = _parser().parse_args(["--out", "/tmp/x", "--only", "dcim.site,dcim.device"])
    assert ns.content_types == "dcim.site,dcim.device"
