"""ARCH-09a: :class:`ResolverFieldError` contract tests.

We pin the four attributes and the single-line render so audit-row
consumers can rely on the format. Inheriting from ``ValueError`` is
checked too, the migration window depends on legacy ``except
ValueError`` clauses still firing.
"""

from __future__ import annotations

import pytest

from nbsnap.natkey.resolver import ResolverFieldError


def test_resolver_field_error_attributes_round_trip() -> None:
    err = ResolverFieldError(
        "slug field empty",
        content_type="dcim.site",
        natural_key=("hall-a",),
        field_name="slug",
        hint="missing source data",
    )

    assert err.content_type == "dcim.site"
    assert err.natural_key == ("hall-a",)
    assert err.field_name == "slug"
    assert err.hint == "missing source data"


def test_resolver_field_error_renders_single_line() -> None:
    """``str()`` formats as ``[ct nk.field] message (hint: hint)``.

    The bracketed prefix is what audit consumers grep on.
    """

    err = ResolverFieldError(
        "field empty",
        content_type="dcim.device",
        natural_key=("hall-a", "d39a"),
        field_name="primary_ip4",
        hint="scope mismatch",
    )

    rendered = str(err)
    assert rendered.startswith("[dcim.device ")
    assert ".primary_ip4]" in rendered
    assert "field empty" in rendered
    assert "scope mismatch" in rendered


def test_resolver_field_error_inherits_value_error() -> None:
    """Legacy ``except ValueError`` clauses still catch the new type."""

    assert issubclass(ResolverFieldError, ValueError)


def test_natural_key_may_be_none() -> None:
    """Resolver may fail before any field has landed in the NK tuple."""

    err = ResolverFieldError(
        "no fields read",
        content_type="dcim.cable",
        natural_key=None,
        field_name="a_terminations",
        hint="schema skew",
    )
    assert err.natural_key is None
    assert "None.a_terminations" in str(err)


def test_pytest_raises_catches_resolver_field_error() -> None:
    """Sanity: pytest.raises with the explicit type works."""

    with pytest.raises(ResolverFieldError):
        raise ResolverFieldError(
            "x",
            content_type="dcim.site",
            natural_key=None,
            field_name="x",
            hint="missing source data",
        )
