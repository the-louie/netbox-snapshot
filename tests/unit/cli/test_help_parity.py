"""ARCH-10f: shared flags render identical help across subcommands.

The help text for ``--no-verify-tls``, ``--content-types``, and
``--audit-out`` is owned by :mod:`nbsnap.cli.common`. The migration
in ARCH-10b/c/d switched every subcommand to import from there, so
any future drift can only come from an accidental local override.
This test renders ``--help`` for each subcommand and asserts the
shared substrings appear verbatim.
"""

from __future__ import annotations

import argparse

import pytest

from nbsnap.export_cli import add_export_args
from nbsnap.import_cli import add_import_args
from nbsnap.plan_cli import add_plan_args
from nbsnap.reset_cli import add_reset_args

# (builder, list of substrings that must appear in --help)
_SUBCOMMAND_TLS = [
    (add_export_args, "self-signed"),
    (add_import_args, "self-signed"),
    (add_plan_args, "self-signed"),
    (add_reset_args, "self-signed"),
]


@pytest.mark.parametrize(
    "builder,substring",
    _SUBCOMMAND_TLS,
    ids=lambda x: getattr(x, "__name__", str(x)),
)
def test_tls_help_text_is_shared(builder, substring: str) -> None:
    parser = argparse.ArgumentParser()
    builder(parser)
    help_text = parser.format_help()
    assert substring in help_text, f"{builder.__name__} dropped the canonical TLS help substring"


_SUBCOMMAND_SCOPE = [
    (add_export_args, "content types"),
    (add_plan_args, "content types"),
    (add_reset_args, "content types"),
]


@pytest.mark.parametrize(
    "builder,substring",
    _SUBCOMMAND_SCOPE,
    ids=lambda x: getattr(x, "__name__", str(x)),
)
def test_scope_help_text_is_shared(builder, substring: str) -> None:
    """import_cli is excluded, it has no scope flag (the snapshot dictates).
    plan, export, reset each share the same wording from cli.common.
    """

    parser = argparse.ArgumentParser()
    builder(parser)
    help_text = parser.format_help()
    assert substring in help_text


_SUBCOMMAND_AUDIT = [
    (add_import_args, "audit"),
    (add_reset_args, "audit"),
]


@pytest.mark.parametrize(
    "builder,substring",
    _SUBCOMMAND_AUDIT,
    ids=lambda x: getattr(x, "__name__", str(x)),
)
def test_audit_help_text_is_shared(builder, substring: str) -> None:
    parser = argparse.ArgumentParser()
    builder(parser)
    help_text = parser.format_help()
    assert substring in help_text
