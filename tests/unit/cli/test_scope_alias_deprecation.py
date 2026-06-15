"""ARCH-10e: ``--only`` still parses but emits a deprecation warning.

The canonical name is ``--content-types``. Scripts still using the
legacy ``--only`` keep working, but they now get a single line to
stderr per invocation so the operator can find and migrate them.
"""

from __future__ import annotations

import argparse
import io

import pytest

from nbsnap.cli.common import add_scope_flags


def _parse(args: list[str], stderr: io.StringIO) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_scope_flags(parser)
    # argparse's _ScopeFlagAction writes directly to sys.stderr; we
    # patch via a small redirector.
    import contextlib

    with contextlib.redirect_stderr(stderr):
        ns = parser.parse_args(args)
    return ns


def test_only_alias_emits_deprecation_warning() -> None:
    stderr = io.StringIO()
    ns = _parse(["--only", "dcim.site"], stderr)

    assert ns.content_types == "dcim.site"
    assert "deprecated" in stderr.getvalue()
    assert "--content-types" in stderr.getvalue()


def test_content_types_canonical_emits_no_warning() -> None:
    """Operators using the canonical name see clean stderr."""

    stderr = io.StringIO()
    ns = _parse(["--content-types", "dcim.site"], stderr)

    assert ns.content_types == "dcim.site"
    assert stderr.getvalue() == ""


def test_no_scope_flag_emits_no_warning() -> None:
    stderr = io.StringIO()
    ns = _parse([], stderr)

    assert ns.content_types is None
    assert stderr.getvalue() == ""
