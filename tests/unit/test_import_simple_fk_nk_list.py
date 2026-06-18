"""Tests for task #24: simple FK with NK in single-element list form.

The rescue-10 run shipped `ipam.iprange.role: ["kea-participant"]`
unresolved at NetBox, causing 86 IPRange POSTs to fail with
HTTP 400. Investigation showed the main-loop resolver actually
DOES handle the case correctly (resolve_simple_fk normalises
the list to a tuple and looks it up against the destination
index). The rescue-10 failure was a side-effect of the look-ahead
path bypassing `_resolve_body` (task #22), not a bug in the
main loop.

These tests pin the contract so a future refactor cannot
re-introduce the regression:

1. A `role: ["kea-participant"]` body with a populated NKIndex
   resolves to the integer id, NOT the original list.
2. The same body with an EMPTY NKIndex (no destination records,
   no snapshot) cleanly DROPS the field via the existing
   warn-and-drop path, so the POST does not include role at all.
3. The same body when look-ahead is wired AND the snapshot has
   the role record creates the role on demand and resolves the
   FK, even though the destination index started empty (the
   case fixed by #22).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.driver import _resolve_body
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.natkey.registry import default as default_registry
from nbsnap.schema.openapi import OpenAPI


def _schema_with_iprange_role() -> OpenAPI:
    """A minimal schema where ipam.iprange has a `role` FK that
    points at ipam.role. Mirrors the NetBox 4.6.2 shape that the
    rescue-10 snapshot was exported against."""

    return OpenAPI(
        {
            "components": {
                "schemas": {
                    "IPRange": {
                        "type": "object",
                        "properties": {
                            "id": {},
                            "start_address": {"type": "string"},
                            "end_address": {"type": "string"},
                            "role": {
                                "allOf": [{"$ref": "#/components/schemas/BriefRole"}],
                                "nullable": True,
                            },
                        },
                    },
                    "PaginatedIPRangeList": {
                        "properties": {
                            "results": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/IPRange"},
                            }
                        }
                    },
                    "BriefRole": {
                        "type": "object",
                        "properties": {"id": {}, "slug": {}},
                    },
                }
            },
            "paths": {
                "/api/ipam/ip-ranges/": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/PaginatedIPRangeList"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "properties": {
                                            "start_address": {},
                                            "end_address": {},
                                            "role": {},
                                        }
                                    }
                                }
                            }
                        }
                    },
                },
                "/api/ipam/roles/": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"properties": {"id": {}, "slug": {}}}
                                    }
                                }
                            }
                        }
                    },
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {"schema": {"properties": {"slug": {}}}}
                            }
                        }
                    },
                },
            },
        }
    )


def test_role_resolves_when_dest_index_is_populated() -> None:
    """The headline case: destination has the role records,
    `role: ["kea-participant"]` resolves to the integer id 8."""

    index = NKIndex()
    # Pretend the ipam.role phase already populated the index.
    for slug, rid in [
        ("kea-bootstrap", 5),
        ("kea-crew", 6),
        ("kea-dist-mgmt", 7),
        ("kea-participant", 8),
    ]:
        index.insert("ipam.role", (slug,), rid)
    index._built_cts.add("ipam.role")

    http = MagicMock(get_all=MagicMock(return_value=iter([])))

    body = {
        "start_address": "92.33.40.137/26",
        "end_address": "92.33.40.190/26",
        "role": ["kea-participant"],
    }
    out = _resolve_body(
        "ipam.iprange",
        body,
        _schema_with_iprange_role(),
        index,
        http,
        default_registry(),
    )
    assert out["role"] == 8


def test_role_dropped_when_dest_and_snapshot_both_miss() -> None:
    """With no destination records AND no snapshot, the field
    is dropped from the resolved body via the warn-and-drop
    path. NetBox never sees the unresolved list."""

    http = MagicMock(get_all=MagicMock(return_value=iter([])))
    body = {
        "start_address": "1.0.0.1/24",
        "end_address": "1.0.0.255/24",
        "role": ["ghost-role"],
    }
    out = _resolve_body(
        "ipam.iprange",
        body,
        _schema_with_iprange_role(),
        NKIndex(),
        http,
        default_registry(),
    )
    # Field dropped.
    assert "role" not in out
    # Other fields survive.
    assert out["start_address"] == "1.0.0.1/24"


def test_role_created_on_demand_when_snapshot_has_it() -> None:
    """With the look-ahead wired in (task #22), an unresolved
    role NK that the destination misses gets created on demand
    from the snapshot tier and the iprange ships with the
    resolved id."""

    http = MagicMock()
    http.get_all.return_value = iter([])
    http.post.return_value = {"id": 99, "slug": "kea-participant"}

    snapshot_index = SnapshotIndex()
    snapshot_index._by_key[("ipam.role", ("kea-participant",))] = {
        "name": "kea-participant",
        "slug": "kea-participant",
    }

    body = {
        "start_address": "92.33.40.137/26",
        "end_address": "92.33.40.190/26",
        "role": ["kea-participant"],
    }
    out = _resolve_body(
        "ipam.iprange",
        body,
        _schema_with_iprange_role(),
        NKIndex(),
        http,
        default_registry(),
        snapshot_index=snapshot_index,
        processing_stack=set(),
        deferred_queue=[],
        current_nk=("92.33.40.137/26", "92.33.40.190/26"),
    )
    # role resolved to the newly-created id 99.
    assert out["role"] == 99
    # And the role POST fired exactly once.
    http.post.assert_called_once()
