"""ARCH-05f: ``plan_cli._parse_scope`` validates content types at parse time.

The CLI's old behaviour: accept any string in ``--content-types``,
quietly proceed, fail later on the wire when NetBox returned 404
for the bogus endpoint. ARCH-05f catches typos at the parser so
the operator's error message names the offending token directly.
"""

from __future__ import annotations

import pytest

from nbsnap.plan_cli import _parse_scope
from nbsnap.schema.content_type import InvalidContentTypeError


def test_empty_string_falls_back_to_default_scope() -> None:
    """``None`` and ``""`` both yield the renderer-minimum default."""

    from nbsnap.plan_cli import DEFAULT_SCOPE

    assert _parse_scope(None) == set(DEFAULT_SCOPE)
    assert _parse_scope("") == set(DEFAULT_SCOPE)


def test_valid_comma_list_parses() -> None:
    scope = _parse_scope("dcim.site,dcim.device")
    assert scope == {"dcim.site", "dcim.device"}


def test_whitespace_around_tokens_is_tolerated() -> None:
    scope = _parse_scope(" dcim.site , dcim.device ")
    assert scope == {"dcim.site", "dcim.device"}


def test_unknown_content_type_raises_at_parse_time() -> None:
    with pytest.raises(InvalidContentTypeError) as exc:
        _parse_scope("dcim.devic")
    assert exc.value.raw == "dcim.devic"


def test_one_typo_in_a_list_blocks_the_whole_scope() -> None:
    """A single bad token fails the parse; the operator sees the
    error on the bad token even when surrounded by valid ones."""

    with pytest.raises(InvalidContentTypeError) as exc:
        _parse_scope("dcim.site,dcim.devic,dcim.device")
    assert exc.value.raw == "dcim.devic"
