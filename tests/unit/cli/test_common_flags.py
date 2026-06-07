"""ARCH-10a: behaviour tests for :mod:`nbsnap.cli.common`.

Each builder is exercised in isolation against a fresh parser so
the assertions pin the *contract*, the canonical flag name and the
default value, not where the flag happens to land alphabetically.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from nbsnap.cli.common import add_audit_flags, add_scope_flags, add_tls_flags


def _empty_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser()


def test_add_tls_flags_no_verify_tls_default_false() -> None:
    parser = _empty_parser()
    add_tls_flags(parser)

    ns = parser.parse_args([])
    assert ns.no_verify_tls is False

    ns = parser.parse_args(["--no-verify-tls"])
    assert ns.no_verify_tls is True


def test_add_scope_flags_canonical_name() -> None:
    parser = _empty_parser()
    add_scope_flags(parser)

    ns = parser.parse_args([])
    assert ns.content_types is None

    ns = parser.parse_args(["--content-types", "dcim.site,dcim.device"])
    assert ns.content_types == "dcim.site,dcim.device"


def test_add_scope_flags_only_is_an_alias() -> None:
    """``--only`` writes to the same dest as ``--content-types``.

    The deprecation warning is ARCH-10e's job; here we just confirm
    the values land in the same namespace attribute.
    """

    parser = _empty_parser()
    add_scope_flags(parser)

    ns = parser.parse_args(["--only", "dcim.site"])
    assert ns.content_types == "dcim.site"


def test_add_audit_flags_defaults() -> None:
    parser = _empty_parser()
    add_audit_flags(parser)

    ns = parser.parse_args([])
    assert ns.audit_out is None
    assert ns.audit_fsync is False


def test_add_audit_flags_explicit() -> None:
    parser = _empty_parser()
    add_audit_flags(parser)

    ns = parser.parse_args(["--audit-out", "/tmp/x.jsonl", "--audit-fsync"])
    assert ns.audit_out == Path("/tmp/x.jsonl")
    assert ns.audit_fsync is True


@pytest.mark.parametrize(
    "builder,expected_substring",
    [
        (add_tls_flags, "self-signed"),
        (add_scope_flags, "content types"),
        (add_audit_flags, "audit"),
    ],
)
def test_each_builder_emits_descriptive_help(
    builder, expected_substring: str
) -> None:
    """The canonical help text mentions the human-readable concept.

    A future refactor that copy-pastes the builder elsewhere is
    unlikely to also copy the help string verbatim; this is a soft
    lock that catches a help-text drift.
    """

    parser = _empty_parser()
    builder(parser)
    help_text = parser.format_help()
    assert expected_substring in help_text
