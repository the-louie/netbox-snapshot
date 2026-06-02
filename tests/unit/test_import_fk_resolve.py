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


def test_safe_resolve_m2m_drops_missing_items() -> None:
    """One missing m2m item must not lose the whole list."""

    from nbsnap.import_.driver import _safe_resolve_m2m


    http = MagicMock()
    http.get_all.return_value = iter([])
    index = NKIndex()
    index.insert("extras.tag", ("present",), 7)
    ids = _safe_resolve_m2m(
        [["present"], ["missing"]],
        "extras.tag",
        index,
        http,
        default_registry(),
        "dcim.device",
        "tags",
    )
    assert ids == [7]


def test_resolve_body_keeps_other_fields_when_m2m_target_missing() -> None:
    """A missing tag must drop the m2m entry, not abort the whole record."""

    from nbsnap.import_.driver import _resolve_body
    from nbsnap.schema.openapi import OpenAPI


    http = MagicMock()
    http.get_all.return_value = iter([])

    openapi = OpenAPI(
        {
            "components": {
                "schemas": {
                    "Device": {
                        "type": "object",
                        "properties": {
                            "id": {},
                            "name": {"type": "string"},
                            "tags": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/NestedTag"},
                            },
                        },
                    },
                    "PaginatedDeviceList": {
                        "properties": {
                            "results": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Device"},
                            }
                        }
                    },
                    "NestedTag": {
                        "type": "object",
                        "properties": {"id": {}, "slug": {}},
                    },
                }
            },
            "paths": {
                "/api/dcim/devices/": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/PaginatedDeviceList"
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "/api/extras/tags/": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "properties": {"id": {}, "slug": {}}
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
            },
        }
    )
    index = NKIndex()
    body = {"name": "d39a", "tags": [["snmpv2"]]}
    resolved = _resolve_body(
        "dcim.device", body, openapi, index, http, default_registry()
    )
    assert resolved["name"] == "d39a"
    assert resolved["tags"] == []
