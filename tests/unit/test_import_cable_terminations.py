"""Tests for task #25: Cable a/b_terminations key conversion.

The snapshot stores cable terminations as:

    "a_terminations": [{"object_natural_key": [...nk...],
                       "object_type": "dcim.interface"}]

NetBox's write API rejects this with HTTP 400 because the
field name expected on write is `object_id`, not
`object_natural_key`. The resolver pre-pass walks each
termination dict and rewrites it.

Three behaviours pinned here:

1. A termination dict with a resolvable NK gets rewritten to
   `{"object_type": ..., "object_id": <int>}`.
2. A terminations list where every item misses results in the
   whole field being dropped (NetBox treats `[]` as a
   validation error; dropping surfaces the cleaner "required"
   error message).
3. A list-of-dict field that does NOT carry the termination
   shape (e.g. unrelated future structured fields) is left
   untouched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.driver import _resolve_termination_lists
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.natkey.registry import default as default_registry
from nbsnap.schema.openapi import OpenAPI


def _minimal_schema() -> OpenAPI:
    """A trim schema, the termination resolver only needs the
    field_spec / iter_endpoints surface for the look-ahead
    callout."""

    return OpenAPI(
        {
            "components": {
                "schemas": {
                    "Cable": {
                        "type": "object",
                        "properties": {"id": {}},
                    }
                }
            },
            "paths": {},
        }
    )


def _call(body: dict, dest: NKIndex | None = None) -> dict:
    return _resolve_termination_lists(
        body,
        _minimal_schema(),
        dest or NKIndex(),
        MagicMock(get_all=MagicMock(return_value=iter([]))),
        default_registry(),
        snapshot_index=SnapshotIndex(),
        processing_stack=set(),
        deferred_queue=[],
        current_nk=("c1",),
        auditor=None,
        owner_ct="dcim.cable",
    )


def test_terminations_with_resolvable_nk_get_object_id() -> None:
    """The headline case: the snapshot's `object_natural_key`
    is replaced with `object_id` carrying the destination's
    integer interface id."""

    dest = NKIndex()
    dest.insert("dcim.interface", (("dist-a",), "Ethernet0"), 42)

    body = {
        "a_terminations": [
            {
                "object_natural_key": (("dist-a",), "Ethernet0"),
                "object_type": "dcim.interface",
            }
        ],
        "b_terminations": [
            {
                "object_natural_key": (("dist-b",), "Ethernet1"),
                "object_type": "dcim.interface",
            }
        ],
        "label": "Cable-1",
    }
    dest.insert("dcim.interface", (("dist-b",), "Ethernet1"), 43)

    out = _call(body, dest=dest)
    assert out["a_terminations"] == [
        {"object_type": "dcim.interface", "object_id": 42},
    ]
    assert out["b_terminations"] == [
        {"object_type": "dcim.interface", "object_id": 43},
    ]
    # Non-termination fields untouched.
    assert out["label"] == "Cable-1"


def test_terminations_with_all_misses_drop_the_field() -> None:
    """If every termination item misses the destination index
    AND the snapshot, the field is removed from the body. A
    cable POST with `a_terminations: []` would surface as
    "required field is empty"; dropping the key surfaces the
    cleaner "required" error message."""

    body = {
        "a_terminations": [
            {
                "object_natural_key": (("ghost",), "Ethernet0"),
                "object_type": "dcim.interface",
            }
        ],
    }
    out = _call(body)
    assert "a_terminations" not in out


def test_terminations_with_some_misses_keeps_resolvable_items() -> None:
    """A mixed list keeps the resolvable items and drops the
    misses. This matches NetBox's "every termination is a real
    endpoint" contract while not punishing the whole cable
    when one endpoint is in scope and another is not."""

    dest = NKIndex()
    dest.insert("dcim.interface", (("dist-a",), "ok-iface"), 42)

    body = {
        "a_terminations": [
            {"object_natural_key": (("dist-a",), "ok-iface"), "object_type": "dcim.interface"},
            {"object_natural_key": (("ghost",), "missing"), "object_type": "dcim.interface"},
        ],
    }
    out = _call(body, dest=dest)
    assert out["a_terminations"] == [
        {"object_type": "dcim.interface", "object_id": 42},
    ]


def test_non_termination_list_field_passes_through() -> None:
    """A list-of-dict field that does NOT carry the termination
    `object_natural_key` + `object_type` keys is not a cable
    termination. Leave it untouched."""

    body = {
        "tags": [{"slug": "foo"}, {"slug": "bar"}],
    }
    out = _call(body)
    assert out["tags"] == [{"slug": "foo"}, {"slug": "bar"}]


def test_malformed_item_skipped_when_sibling_is_valid() -> None:
    """When a list HAS at least one valid termination dict but
    also carries a malformed sibling (missing `object_type` or
    `object_natural_key`, or not a dict at all), the resolver
    keeps the valid item and drops the malformed one. The
    defensive skip prevents one bad row from aborting the
    whole import."""

    dest = NKIndex()
    dest.insert("dcim.interface", (("dist-a",), "ok-iface"), 42)

    body = {
        "a_terminations": [
            # Valid termination dict.
            {"object_natural_key": (("dist-a",), "ok-iface"), "object_type": "dcim.interface"},
            # Malformed: missing object_type.
            {"object_natural_key": (("dist-b",), "missing-type")},
            # Malformed: not a dict at all.
            "stringly-typed-junk",
        ],
    }
    out = _call(body, dest=dest)
    # Only the resolvable item survives, rewritten to NetBox shape.
    assert out["a_terminations"] == [
        {"object_type": "dcim.interface", "object_id": 42},
    ]


def test_non_termination_list_with_no_matching_items_passes_through() -> None:
    """When NO item in a list-of-dict field carries the
    termination keys, the field is treated as a non-termination
    list (e.g. tags carrying brief refs) and left untouched.
    This is the negative half of the detection rule."""

    body = {
        "some_other_list": [
            {"object_type": "dcim.interface"},  # has type, no nk
            "not-a-dict",
        ],
    }
    out = _call(body)
    assert out["some_other_list"] == body["some_other_list"]


def test_empty_termination_list_passes_through_unchanged() -> None:
    """An empty `a_terminations: []` in the snapshot is not a
    termination list per the spot-check (no items have the
    expected keys), so it passes through untouched. NetBox's
    own validation surfaces the "required" error to the
    operator, which is the friendliest signal for an
    intentionally-empty cable endpoint."""

    body = {"a_terminations": []}
    out = _call(body)
    assert out["a_terminations"] == []
