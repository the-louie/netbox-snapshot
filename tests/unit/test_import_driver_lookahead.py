"""FEAT-36b5 driver-wiring tests.

One key behaviour: when the destination misses on an FK, the
driver consults the snapshot via the look-ahead resolver and
creates the missing parent before the child upsert proceeds.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.driver import _resolve_body
from nbsnap.import_.lookahead import DeferredFK
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.natkey.registry import default as default_registry
from nbsnap.schema.openapi import OpenAPI


def _device_schema() -> OpenAPI:
    """Minimal schema with a Device that has an FK to Site."""

    return OpenAPI({
        "components": {
            "schemas": {
                "Device": {
                    "type": "object",
                    "properties": {
                        "id": {},
                        "name": {"type": "string"},
                        "site": {"$ref": "#/components/schemas/BriefSite"},
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
                "BriefSite": {
                    "type": "object",
                    "properties": {"id": {}, "slug": {}},
                },
            }
        },
        "paths": {
            "/api/dcim/devices/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {
                        "$ref": "#/components/schemas/PaginatedDeviceList"
                    }}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {"properties": {
                        "name": {}, "site": {},
                    }}}
                }}},
            },
            "/api/dcim/sites/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {"properties": {"id": {}, "slug": {}}}}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {"properties": {"slug": {}}}}
                }}},
            },
        }
    })


def test_lookahead_creates_missing_parent_during_resolve_body() -> None:
    """A Device references Site by NK; destination is empty.
    The driver's _resolve_body uses the look-ahead path to
    create the Site on the destination first, then resolves
    the FK to the new Site's id."""

    http = MagicMock()
    http.get_all.return_value = iter([])  # destination is empty
    http.post.return_value = {"id": 42, "slug": "hall-d", "name": "Hall-D"}

    snapshot_index = SnapshotIndex()
    snapshot_index._by_key[("dcim.site", ("hall-d",))] = {
        "name": "Hall-D", "slug": "hall-d",
    }

    dest_index = NKIndex()
    deferred_queue: list[DeferredFK] = []
    processing_stack: set = set()

    body = {"name": "d39a", "site": ["hall-d"]}

    resolved = _resolve_body(
        "dcim.device",
        body,
        _device_schema(),
        dest_index,
        http,
        default_registry(),
        snapshot_index=snapshot_index,
        processing_stack=processing_stack,
        deferred_queue=deferred_queue,
        current_nk=(("hall-d",), "d39a"),
    )

    # The site FK is now the destination id of the newly-created
    # Site (42), not the snapshot NK tuple.
    assert resolved["site"] == 42
    # The look-ahead path created the Site, so http.post was
    # called for /api/dcim/sites/.
    assert http.post.called
    # No cycle was detected, so no DeferredFK accumulated.
    assert deferred_queue == []


def test_lookahead_drops_unrecoverable_fk_with_warning() -> None:
    """Both indexes miss, the FK gets dropped via the existing
    warn-and-drop path. No exception, no new DeferredFK."""

    from nbsnap.import_.driver import _WARNED_MISSING_FK

    _WARNED_MISSING_FK.clear()

    http = MagicMock()
    http.get_all.return_value = iter([])
    snapshot_index = SnapshotIndex()  # empty
    dest_index = NKIndex()

    body = {"name": "d39a", "site": ["ghost-site"]}

    resolved = _resolve_body(
        "dcim.device",
        body,
        _device_schema(),
        dest_index,
        http,
        default_registry(),
        snapshot_index=snapshot_index,
        processing_stack=set(),
        deferred_queue=[],
        current_nk=(("ghost-site",), "d39a"),
    )

    # site is dropped from the resolved body.
    assert "site" not in resolved
    assert resolved["name"] == "d39a"
