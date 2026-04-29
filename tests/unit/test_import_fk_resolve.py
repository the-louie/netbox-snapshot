"""FEAT-20a/b/c FK resolver tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nbsnap.import_.fk_resolve import normalise_nk, resolve_m2m, resolve_simple_fk
from nbsnap.import_.nk_index import NKIndex
from nbsnap.natkey.registry import default as default_registry


def test_normalise_nk_round_trips_lists_to_tuples() -> None:
    assert normalise_nk(["a", ["b", "c"]]) == ("a", ("b", "c"))


def test_resolve_simple_fk_finds_in_index() -> None:
    http = MagicMock()
    http.get_all.return_value = iter([])
    index = NKIndex()
    index.insert("dcim.site", ("hall-d",), 5)
    rid = resolve_simple_fk(
        ("hall-d",),
        "dcim.site",
        index,
        http=http,
        registry=default_registry(),
    )
    assert rid == 5


def test_resolve_simple_fk_raises_when_missing() -> None:
    http = MagicMock()
    http.get_all.return_value = iter([])
    index = NKIndex()
    with pytest.raises(KeyError):
        resolve_simple_fk(
            ("unknown",),
            "dcim.site",
            index,
            http=http,
            registry=default_registry(),
        )


def test_resolve_m2m_returns_list_of_ids() -> None:
    http = MagicMock()
    http.get_all.return_value = iter([])
    index = NKIndex()
    index.insert("extras.tag", ("a",), 1)
    index.insert("extras.tag", ("b",), 2)
    ids = resolve_m2m(
        [["a"], ["b"]],
        "extras.tag",
        index,
        http=http,
        registry=default_registry(),
    )
    assert ids == [1, 2]
