"""ARCH-04c: ``--plugins-dir`` lands on both export and import CLIs.

We only assert the argparse contract here, the registry-loading
behaviour is covered by tests/unit/natkey/test_registry_with_plugins
and the integration test ARCH-04d.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from nbsnap.export_cli import add_export_args
from nbsnap.import_cli import add_import_args


def _parser(builder) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    builder(parser)
    return parser


def test_export_cli_exposes_plugins_dir() -> None:
    ns = _parser(add_export_args).parse_args(
        ["--out", "/tmp/x", "--plugins-dir", "/tmp/plugins"]
    )
    assert ns.plugins_dir == Path("/tmp/plugins")


def test_export_cli_plugins_dir_defaults_to_none() -> None:
    ns = _parser(add_export_args).parse_args(["--out", "/tmp/x"])
    assert ns.plugins_dir is None


def test_import_cli_exposes_plugins_dir() -> None:
    ns = _parser(add_import_args).parse_args(
        ["--in", "/tmp/snap", "--plugins-dir", "/tmp/plugins"]
    )
    assert ns.plugins_dir == Path("/tmp/plugins")


def test_import_cli_plugins_dir_defaults_to_none() -> None:
    ns = _parser(add_import_args).parse_args(["--in", "/tmp/snap"])
    assert ns.plugins_dir is None
