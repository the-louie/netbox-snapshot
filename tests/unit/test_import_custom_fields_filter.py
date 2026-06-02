"""Tests for task #28: filter unknown custom_fields keys at the write boundary.

When the look-ahead path fires for racks or devices BEFORE the
extras.customfield main phase runs, the body it lifts from the
snapshot still carries `custom_fields: {...}` with keys whose
definitions do not yet exist on the destination. NetBox
rejects the POST/PATCH with HTTP 400:
`Custom field 'switch_count' does not exist for this object
type`.

The filter consults a lazily-populated registry of which custom
fields the destination knows about for each content type and
drops the unknown keys before the write fires. The main phase
for that content type later PATCHes the values back in once
the field definitions exist.

Five behaviours pinned here:

1. The destination registry is populated from one
   `extras/custom-fields/` fetch and cached per http base URL.
2. Both the legacy `["dcim.site", ...]` shape and the newer
   `[{"value": "dcim.site"}, ...]` shape for
   `CustomField.object_types` are accepted.
3. A body without `custom_fields` passes through unchanged.
4. A body whose CF keys are all unknown ends up with
   `custom_fields: {}` (legal in NetBox).
5. A body whose CF keys are a mix of known and unknown keeps
   only the known ones.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.upsert import (
    _filter_custom_fields,
    _known_custom_fields_for,
)


def _fake_http(rows: list, *, base_url: str = "https://dest.example/") -> MagicMock:
    """A MagicMock NetboxHTTP whose `get_all` yields the
    provided rows when the customfield endpoint is queried.

    The mock carries `_cf_cache=None` and
    `_cf_cache_failed=False` so the upsert helpers' instance-
    scoped cache (REFACTOR-06) treats each fake client as
    cold.
    """

    http = MagicMock()
    http.base_url = base_url
    http._cf_cache = None
    http._cf_cache_failed = False
    http.get_all.side_effect = lambda endpoint: iter(
        rows if "custom-fields" in endpoint else []
    )
    return http


# ---------------------------------------------------------------------------
# Registry behaviour
# ---------------------------------------------------------------------------


def test_registry_indexes_legacy_string_shape() -> None:
    """NetBox 4.6.x emits `object_types: ["dcim.site",
    "dcim.rack"]`. The registry pulls the names out."""

    rows = [
        {"name": "switch_count", "object_types": ["dcim.site", "dcim.rack"]},
    ]
    http = _fake_http(rows)
    assert _known_custom_fields_for(http, "dcim.rack") == {"switch_count"}
    assert _known_custom_fields_for(http, "dcim.site") == {"switch_count"}


def test_registry_indexes_dict_shape() -> None:
    """Future NetBox versions may emit
    `object_types: [{"value": "dcim.site"}, ...]`. The
    registry treats both shapes interchangeably."""

    rows = [
        {"name": "cpu_cores_total",
         "object_types": [{"value": "dcim.device"}]},
    ]
    http = _fake_http(rows)
    assert _known_custom_fields_for(http, "dcim.device") == {"cpu_cores_total"}


def test_registry_cache_avoids_second_fetch() -> None:
    """The first lookup triggers an http.get_all, the second
    lookup hits the cache. Save round-trips when many records
    of the same CT go through the filter."""

    rows = [{"name": "x", "object_types": ["dcim.site"]}]
    http = _fake_http(rows)
    _known_custom_fields_for(http, "dcim.site")
    _known_custom_fields_for(http, "dcim.site")
    # Only one network round-trip even though we asked twice.
    assert http.get_all.call_count == 1


def test_registry_returns_none_on_fetch_failure() -> None:
    """If the customfield list endpoint is unavailable, the
    registry returns None (the "do not filter" signal). This
    is safer than returning an empty set, which would tell
    the filter to drop every CF key and silently destroy
    custom-field data."""

    http = MagicMock()
    http.base_url = "https://dest.example/"
    http.get_all.side_effect = RuntimeError("offline")
    assert _known_custom_fields_for(http, "dcim.rack") is None


def test_filter_passes_body_unchanged_when_registry_failed() -> None:
    """When the registry could not be loaded, the filter
    leaves the body untouched so the operator sees any real
    HTTP 400 from NetBox rather than a silent data-loss
    swap to empty custom_fields."""

    http = MagicMock()
    http.base_url = "https://dest.example/"
    http.get_all.side_effect = RuntimeError("offline")
    body = {"custom_fields": {"keep_me": 1}}
    out = _filter_custom_fields(body, http, "dcim.site")
    # No change.
    assert out is body


# ---------------------------------------------------------------------------
# Filter behaviour
# ---------------------------------------------------------------------------


def test_filter_drops_unknown_keys() -> None:
    """Keys that the destination does not know about are
    removed, known keys survive."""

    rows = [{"name": "switch_count", "object_types": ["dcim.rack"]}]
    http = _fake_http(rows)
    body = {
        "name": "C1",
        "custom_fields": {
            "switch_count": 4,        # known
            "ghost_field": "drop me", # unknown
        },
    }
    out = _filter_custom_fields(body, http, "dcim.rack")
    assert out["custom_fields"] == {"switch_count": 4}
    # Other fields untouched.
    assert out["name"] == "C1"


def test_filter_returns_same_body_when_no_changes() -> None:
    """If every key is already known, the filter does not
    allocate a new dict. Microbench-friendly behaviour for
    the common no-op case after the customfield phase ran."""

    rows = [{"name": "switch_count", "object_types": ["dcim.rack"]}]
    http = _fake_http(rows)
    body = {"custom_fields": {"switch_count": 4}}
    out = _filter_custom_fields(body, http, "dcim.rack")
    # `out is body` identity check, the filter returns the
    # original dict when nothing changed.
    assert out is body


def test_filter_passes_body_without_custom_fields() -> None:
    """A body that does not carry a custom_fields dict is
    untouched."""

    http = _fake_http([])
    body = {"name": "no-cf"}
    out = _filter_custom_fields(body, http, "dcim.site")
    assert out is body


def test_filter_handles_all_unknown_keys() -> None:
    """When every CF key is unknown, the body ends up with
    `custom_fields: {}`. NetBox accepts empty dicts; the main
    customfield phase later PATCHes the values back in."""

    rows: list = []  # destination has no CFs at all
    http = _fake_http(rows)
    body = {
        "name": "C1",
        "custom_fields": {"switch_count": 4, "other": 1},
    }
    out = _filter_custom_fields(body, http, "dcim.rack")
    assert out["custom_fields"] == {}
    assert out["name"] == "C1"


def test_two_instances_keep_separate_caches() -> None:
    """REFACTOR-06: two NetboxHTTP-like clients with the same
    base_url maintain independent custom-field caches. Before
    the instance-scoped move, the second client would see the
    first's cache and skip its own GET."""

    http_a = _fake_http(
        [{"name": "field_a", "object_types": ["dcim.site"]}],
        base_url="https://dest.example/",
    )
    http_b = _fake_http(
        [{"name": "field_b", "object_types": ["dcim.site"]}],
        base_url="https://dest.example/",
    )
    assert _known_custom_fields_for(http_a, "dcim.site") == {"field_a"}
    assert _known_custom_fields_for(http_b, "dcim.site") == {"field_b"}
