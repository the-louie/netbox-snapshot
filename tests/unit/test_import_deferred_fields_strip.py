"""Tests for task #30: strip deferred-edge fields from POST bodies.

Phase-2 was designed to PATCH cycle-closing FKs (e.g.
`Device.primary_ip4`) after both endpoints exist on the
destination. Phase-1 was supposed to POST WITHOUT those FKs.
Before this fix, Phase-1 actually shipped the resolved id in
the body, and NetBox refused because the IPAddress was not
yet bound to the device's interface.

`_strip_deferred_fields_and_queue` runs at the end of
`_resolve_body`. For every field listed in
`manifest.deferred_edges` for this content type:

1. The field is removed from the body so the POST proceeds
   without it.
2. A `DeferredFK` is appended to the queue so Phase-2 PATCHes
   the field in once both endpoints exist.

Four behaviours pinned here:

1. A field listed in deferred_fields_by_ct is stripped from
   the resolved body.
2. The corresponding DeferredFK is appended to the queue with
   the original snapshot NK as the target.
3. A field NOT listed is left alone.
4. A field whose value is already None is dropped from the
   body but no DeferredFK is queued (nothing to defer).
"""

from __future__ import annotations

from nbsnap.import_.driver import _strip_deferred_fields_and_queue
from nbsnap.import_.lookahead import DeferredFK
from nbsnap.schema.openapi import OpenAPI


def _device_schema() -> OpenAPI:
    """A minimal schema where dcim.device has a primary_ip4 FK
    to ipam.ipaddress. Enough surface for the strip helper to
    resolve the target content type via `field_spec`."""

    return OpenAPI({
        "components": {
            "schemas": {
                "Device": {
                    "type": "object",
                    "properties": {
                        "id": {},
                        "name": {"type": "string"},
                        "primary_ip4": {
                            "allOf": [{"$ref": "#/components/schemas/BriefIPAddress"}],
                            "nullable": True,
                        },
                    },
                },
                "PaginatedDeviceList": {
                    "properties": {"results": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Device"},
                    }}
                },
                "BriefIPAddress": {
                    "type": "object",
                    "properties": {"id": {}, "address": {}},
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
                        "name": {}, "primary_ip4": {},
                    }}}
                }}},
            },
            "/api/ipam/ip-addresses/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {
                        "properties": {"id": {}, "address": {}}
                    }}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {"properties": {"address": {}}}}
                }}},
            },
        }
    })


def test_strip_deferred_field_and_queue_entry() -> None:
    """A device with primary_ip4 set + primary_ip4 in the
    deferred-fields-by-ct index gets stripped, and a
    DeferredFK lands on the queue."""

    original_body = {
        "name": "d39a",
        # The original snapshot value is the IPAddress NK tuple.
        "primary_ip4": ["172.16.1.10/24", "dcim.interface",
                        [[["d"], "D39A"], "Vlan600"]],
    }
    resolved = {
        "name": "d39a",
        # After _resolve_body, primary_ip4 holds the destination id.
        "primary_ip4": 99,
    }
    queue: list = []
    out = _strip_deferred_fields_and_queue(
        resolved,
        content_type="dcim.device",
        current_nk=(("hall-d",), "d39a"),
        original_body=original_body,
        deferred_fields_by_ct={"dcim.device": {"primary_ip4"}},
        deferred_queue=queue,
        openapi=_device_schema(),
    )
    # primary_ip4 stripped.
    assert "primary_ip4" not in out
    assert out["name"] == "d39a"
    # DeferredFK queued with the original snapshot NK as target.
    assert len(queue) == 1
    entry = queue[0]
    assert isinstance(entry, DeferredFK)
    assert entry.child_content_type == "dcim.device"
    assert entry.field_name == "primary_ip4"
    assert entry.target_content_type == "ipam.ipaddress"


def test_field_not_in_deferred_index_passes_through() -> None:
    """A field that the manifest did not mark as deferred is
    left in the resolved body."""

    resolved = {"name": "d39a", "primary_ip4": 99}
    queue: list = []
    out = _strip_deferred_fields_and_queue(
        resolved,
        content_type="dcim.device",
        current_nk=(("hall-d",), "d39a"),
        original_body=resolved,
        # primary_ip4 NOT marked deferred for any CT.
        deferred_fields_by_ct={"dcim.devicerole": {"parent"}},
        deferred_queue=queue,
        openapi=_device_schema(),
    )
    assert out is resolved
    assert queue == []


def test_field_with_none_value_is_dropped_without_queueing() -> None:
    """A deferred field whose value is already None gets
    removed from the body (so the body stays consistent with
    'we PATCH this later') but NOT queued because there is
    nothing to defer."""

    resolved = {"name": "d39a", "primary_ip4": None}
    queue: list = []
    out = _strip_deferred_fields_and_queue(
        resolved,
        content_type="dcim.device",
        current_nk=(("hall-d",), "d39a"),
        original_body={"name": "d39a", "primary_ip4": None},
        deferred_fields_by_ct={"dcim.device": {"primary_ip4"}},
        deferred_queue=queue,
        openapi=_device_schema(),
    )
    assert "primary_ip4" not in out
    assert out["name"] == "d39a"
    assert queue == []


def test_no_deferred_fields_for_content_type_is_noop() -> None:
    """When the content type has no deferred fields, the
    helper returns the input unchanged without allocating a
    new dict."""

    resolved = {"name": "d39a", "primary_ip4": 99}
    queue: list = []
    out = _strip_deferred_fields_and_queue(
        resolved,
        content_type="dcim.device",
        current_nk=(("hall-d",), "d39a"),
        original_body=resolved,
        deferred_fields_by_ct={"dcim.devicerole": {"parent"}},
        deferred_queue=queue,
        openapi=_device_schema(),
    )
    # Identity check: helper did not allocate a new dict.
    assert out is resolved


def test_missing_deferred_fields_by_ct_is_noop() -> None:
    """A `None` deferred_fields_by_ct (legacy callers) skips
    the strip pass entirely."""

    resolved = {"name": "d39a", "primary_ip4": 99}
    out = _strip_deferred_fields_and_queue(
        resolved,
        content_type="dcim.device",
        current_nk=(("hall-d",), "d39a"),
        original_body=resolved,
        deferred_fields_by_ct=None,
        deferred_queue=[],
        openapi=_device_schema(),
    )
    assert out is resolved


def test_missing_deferred_queue_is_noop() -> None:
    """A `None` deferred_queue also skips the strip pass, the
    helper has nowhere to push the DeferredFK and stripping
    without queueing would lose data silently."""

    resolved = {"name": "d39a", "primary_ip4": 99}
    out = _strip_deferred_fields_and_queue(
        resolved,
        content_type="dcim.device",
        current_nk=(("hall-d",), "d39a"),
        original_body=resolved,
        deferred_fields_by_ct={"dcim.device": {"primary_ip4"}},
        deferred_queue=None,
        openapi=_device_schema(),
    )
    assert out is resolved


def test_dedupe_does_not_push_duplicate_deferred_fk() -> None:
    """A record can flow through `_resolve_body` twice (once
    via look-ahead recursion, once via the main phase). The
    second pass strips the field again but does NOT push a
    duplicate DeferredFK onto the queue."""

    original_body = {
        "name": "d39a",
        "primary_ip4": ["172.16.1.10/24", "dcim.interface",
                        [[["d"], "D39A"], "Vlan600"]],
    }
    queue: list = []

    # First call (e.g. via look-ahead): pushes one entry.
    _strip_deferred_fields_and_queue(
        {"name": "d39a", "primary_ip4": 99},
        content_type="dcim.device",
        current_nk=(("hall-d",), "d39a"),
        original_body=original_body,
        deferred_fields_by_ct={"dcim.device": {"primary_ip4"}},
        deferred_queue=queue,
        openapi=_device_schema(),
    )
    assert len(queue) == 1

    # Second call (e.g. via main phase later): same record,
    # same field. The dedup guard suppresses the second push.
    _strip_deferred_fields_and_queue(
        {"name": "d39a", "primary_ip4": 99},
        content_type="dcim.device",
        current_nk=(("hall-d",), "d39a"),
        original_body=original_body,
        deferred_fields_by_ct={"dcim.device": {"primary_ip4"}},
        deferred_queue=queue,
        openapi=_device_schema(),
    )
    assert len(queue) == 1


# ---------------------------------------------------------------------------
# Integration through _resolve_body
# ---------------------------------------------------------------------------


def test_resolve_body_strips_deferred_field_and_queues_entry() -> None:
    """End-to-end check: `_resolve_body` with a populated
    `deferred_fields_by_ct` strips primary_ip4 and queues a
    DeferredFK. Confirms the wire-up between the public
    entry point and the strip helper."""

    from unittest.mock import MagicMock

    from nbsnap.import_.driver import _resolve_body
    from nbsnap.import_.nk_index import NKIndex
    from nbsnap.import_.snapshot_index import SnapshotIndex
    from nbsnap.natkey.registry import default as default_registry

    # Pretend the IPAddress already exists on the destination
    # with id=99 so resolve_simple_fk returns 99 and the field
    # is in `resolved` before the strip pass.
    dest = NKIndex()
    # Use a tuple-shape NK that matches what normalise_nk
    # produces from the snapshot's list-shape value.
    target_nk = ("172.16.1.10/24", "dcim.interface",
                 ((("d",), "D39A"), "Vlan600"))
    dest.insert("ipam.ipaddress", target_nk, 99)

    body = {
        "name": "d39a",
        "primary_ip4": ["172.16.1.10/24", "dcim.interface",
                        [[["d"], "D39A"], "Vlan600"]],
    }
    queue: list = []
    out = _resolve_body(
        "dcim.device", body, _device_schema(),
        dest, MagicMock(get_all=MagicMock(return_value=iter([]))),
        default_registry(),
        snapshot_index=SnapshotIndex(),
        processing_stack=set(),
        deferred_queue=queue,
        current_nk=(("hall-d",), "d39a"),
        deferred_fields_by_ct={"dcim.device": {"primary_ip4"}},
    )
    # primary_ip4 stripped from the resolved body.
    assert "primary_ip4" not in out
    # Other fields survive.
    assert out["name"] == "d39a"
    # DeferredFK queued.
    assert len(queue) == 1
    assert queue[0].field_name == "primary_ip4"
    assert queue[0].child_content_type == "dcim.device"
