"""Tests for task #23: paired polymorphic-id resolver.

NetBox writes generic FKs as either a unified dict
`{"object_type":..., "object_id":...}` or as a pair of sibling
fields `<prefix>_type` + `<prefix>_id`. The snapshot stores the
paired shape with `_id` carrying the natural key of the target,
not an integer. The pre-pass in `_resolve_body` translates the
NK into the destination's integer id before the write.

Four behaviours pinned here:

1. A paired pattern with a destination hit resolves the `_id`
   to the integer id and leaves the `_type` alone.
2. An already-resolved integer in the `_id` field is left as is
   (no double resolution).
3. A miss on both destination index and snapshot drops BOTH
   fields together, NetBox rejects half-pair writes.
4. Fields not matching the pair pattern (lone `_type` without
   a sibling `_id`, or `_type` carrying a non content-type
   string) pass through untouched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.audit import Auditor, DropCategory
from nbsnap.import_.driver import _resolve_polymorphic_id_pairs
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.natkey.registry import default as default_registry
from nbsnap.schema.openapi import OpenAPI


def _minimal_schema() -> OpenAPI:
    """A schema where dcim.interface has a slug-shaped NK.
    Only the iter_endpoints / field_spec surface is exercised
    here, so a trimmed shape is enough."""

    return OpenAPI({
        "components": {
            "schemas": {
                "Interface": {
                    "type": "object",
                    "properties": {"id": {}, "name": {"type": "string"}},
                },
                "PaginatedInterfaceList": {
                    "properties": {"results": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Interface"},
                    }}
                },
            }
        },
        "paths": {
            "/api/dcim/interfaces/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {
                        "$ref": "#/components/schemas/PaginatedInterfaceList"
                    }}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {"properties": {"name": {}}}}
                }}},
            },
        }
    })


def _call(body: dict, *, dest_index: NKIndex | None = None,
          http: MagicMock | None = None) -> dict:
    """Run the pre-pass with sensible defaults."""

    return _resolve_polymorphic_id_pairs(
        body,
        _minimal_schema(),
        dest_index or NKIndex(),
        http or MagicMock(get_all=MagicMock(return_value=iter([]))),
        default_registry(),
        snapshot_index=SnapshotIndex(),
        processing_stack=set(),
        deferred_queue=[],
        current_nk=("test",),
        auditor=Auditor(),
        owner_ct="ipam.ipaddress",
    )


def test_resolves_paired_polymorphic_id_against_destination() -> None:
    """When the destination NKIndex has the target, the `_id`
    field is replaced with the integer id and `_type` is left
    alone."""

    dest = NKIndex()
    # Pretend the destination already has Interface id=42 keyed
    # by NK ("dist-a", "Ethernet0").
    dest.insert("dcim.interface", ("dist-a", "Ethernet0"), 42)

    body = {
        "address": "10.0.0.1/24",
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": ("dist-a", "Ethernet0"),
    }

    out = _call(body, dest_index=dest)
    assert out["assigned_object_type"] == "dcim.interface"
    assert out["assigned_object_id"] == 42
    # Other fields untouched.
    assert out["address"] == "10.0.0.1/24"


def test_null_id_drops_both_halves_of_pair() -> None:
    """An intentionally-unbound polymorphic FK in the source
    (`assigned_object_id: null` with a non-null
    `assigned_object_type`) must result in BOTH halves
    being dropped from the body. NetBox refuses `..._id:
    null` paired with a non-null `..._type`; the only legal
    shape for an unbound FK is omitting both."""

    body = {
        "address": "10.0.0.1/24",
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": None,
    }
    out = _call(body)
    assert "assigned_object_type" not in out
    assert "assigned_object_id" not in out
    assert out["address"] == "10.0.0.1/24"


def test_integer_id_passes_through_untouched() -> None:
    """If the `_id` field already carries an integer, the
    pre-pass is a no-op for that pair."""

    body = {
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": 99,
    }
    out = _call(body)
    assert out["assigned_object_id"] == 99


def test_miss_on_both_drops_both_halves() -> None:
    """When neither the destination index nor the snapshot has
    the target, BOTH `_type` and `_id` are removed from the
    body. NetBox treats a half-pair write as a validation
    error, so dropping the pair together keeps the write
    legal."""

    body = {
        "address": "10.0.0.1/24",
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": ("ghost-device", "Ethernet0"),
    }
    out = _call(body)
    # The pair is gone.
    assert "assigned_object_type" not in out
    assert "assigned_object_id" not in out
    # Other fields survive.
    assert out["address"] == "10.0.0.1/24"


def test_content_type_type_field_without_sibling_id_passes_through() -> None:
    """A `_type` field carrying a content-type string but with
    NO matching `_id` sibling is not a pair, leave it
    untouched. The sibling check is what disambiguates a pair
    from a stray content-type field."""

    body = {
        # Content-type shaped value, but no `weird_object_id`
        # sibling, so this is NOT a polymorphic pair.
        "weird_object_type": "dcim.interface",
        "other_field": "some-value",
    }
    out = _call(body)
    assert out == body


def test_lone_type_field_with_non_content_type_value_passes_through() -> None:
    """A plain enum-shaped `_type` (no dot in value) is not a
    polymorphic pair even if a sibling `_id` exists."""

    body = {
        "type": "cat6",  # plain enum, no dot
        "name": "Cable1",
    }
    out = _call(body)
    assert out == body


def test_type_field_with_non_content_type_string_passes_through() -> None:
    """A `_type` field that does not look like a content type
    (no `.` in value) is not a polymorphic pair. NetBox uses
    such strings for plain enums like `weight_unit_type`."""

    body = {
        "weight_unit_type": "kg",  # not a content type
        "weight_unit_id": "1.5",   # sibling that happens to exist
    }
    out = _call(body)
    # No change.
    assert out == body


def test_audit_records_out_of_scope_when_target_ct_not_in_snapshot() -> None:
    """A pair whose target content type is not in the snapshot
    at all classifies as OUT_OF_SCOPE. The classifier uses
    `SnapshotIndex.has_content_type` to make this call."""

    auditor = Auditor()
    body = {
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": ("ghost", "iface"),
    }
    # SnapshotIndex is empty, so `has_content_type('dcim.interface')`
    # returns False, so the classifier picks OUT_OF_SCOPE.
    _resolve_polymorphic_id_pairs(
        body,
        _minimal_schema(),
        NKIndex(),
        MagicMock(get_all=MagicMock(return_value=iter([]))),
        default_registry(),
        snapshot_index=SnapshotIndex(),
        processing_stack=set(),
        deferred_queue=[],
        current_nk=("test",),
        auditor=auditor,
        owner_ct="ipam.ipaddress",
    )
    assert len(auditor.events) == 1
    ev = auditor.events[0]
    assert ev.category is DropCategory.OUT_OF_SCOPE


def test_audit_records_missing_from_source_when_snapshot_has_ct_but_not_nk() -> None:
    """A pair whose target CT IS in the snapshot but whose NK
    is not is MISSING_FROM_SOURCE. The distinction matters,
    OUT_OF_SCOPE is documented behaviour, MISSING_FROM_SOURCE
    is a real data gap."""

    auditor = Auditor()
    snapshot_index = SnapshotIndex()
    # Seed a DIFFERENT interface NK so the content type is in
    # scope but the specific lookup misses.
    snapshot_index._by_key[("dcim.interface", ("other", "Ethernet0"))] = {
        "name": "Ethernet0"
    }
    body = {
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": ("ghost", "iface"),
    }
    _resolve_polymorphic_id_pairs(
        body,
        _minimal_schema(),
        NKIndex(),
        MagicMock(get_all=MagicMock(return_value=iter([]))),
        default_registry(),
        snapshot_index=snapshot_index,
        processing_stack=set(),
        deferred_queue=[],
        current_nk=("test",),
        auditor=auditor,
        owner_ct="ipam.ipaddress",
    )
    assert len(auditor.events) == 1
    assert auditor.events[0].category is DropCategory.MISSING_FROM_SOURCE
