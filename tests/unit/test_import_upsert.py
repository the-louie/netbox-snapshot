"""FEAT-21a/b upsert tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.upsert import UpsertOutcome, upsert
from nbsnap.natkey.registry import default as default_registry


def _http_with(behaviour: dict) -> MagicMock:
    http = MagicMock()
    http.get_all.return_value = iter([])
    http.get_one.return_value = behaviour.get("get_one")
    http.post.return_value = behaviour.get("post")
    http.patch.return_value = behaviour.get("patch")
    return http


def test_upsert_creates_when_nk_absent() -> None:
    http = _http_with({"post": {"id": 99}})
    index = NKIndex()
    result = upsert(
        http,
        content_type="dcim.site",
        natural_key=("hall-d",),
        body={"name": "Hall D", "slug": "hall-d"},
        index=index,
        registry=default_registry(),
    )
    assert result.outcome is UpsertOutcome.CREATED
    assert result.destination_id == 99
    assert index.lookup("dcim.site", ("hall-d",)) == 99


def test_upsert_noops_when_values_match() -> None:
    http = _http_with({"get_one": {"id": 7, "name": "Hall D", "slug": "hall-d"}})
    index = NKIndex()
    index.insert("dcim.site", ("hall-d",), 7)
    result = upsert(
        http,
        content_type="dcim.site",
        natural_key=("hall-d",),
        body={"name": "Hall D", "slug": "hall-d"},
        index=index,
        registry=default_registry(),
    )
    assert result.outcome is UpsertOutcome.NOOP
    http.patch.assert_not_called()


def test_upsert_coerces_legacy_enum_dict_in_post_body() -> None:
    """Defensive import-side coerce: an old snapshot's
    `{value, label}` status survives the POST as the bare value.

    Regression for the legacy-compat half of FEAT-36-blocker.
    The canonical fix lives in the export side, but a snapshot
    written before that fix must still upload cleanly.
    """

    http = _http_with({"post": {"id": 99}})
    index = NKIndex()
    upsert(
        http,
        content_type="dcim.site",
        natural_key=("hall-d",),
        body={
            "name": "Hall D",
            "slug": "hall-d",
            "status": {"value": "active", "label": "Active"},
        },
        index=index,
        registry=default_registry(),
    )
    posted_body = http.post.call_args.args[1]
    assert posted_body["status"] == "active"
    assert posted_body["name"] == "Hall D"


def test_upsert_patches_only_changed_fields() -> None:
    http = _http_with({"get_one": {"id": 7, "name": "Hall D", "slug": "hall-d"}})
    index = NKIndex()
    index.insert("dcim.site", ("hall-d",), 7)
    result = upsert(
        http,
        content_type="dcim.site",
        natural_key=("hall-d",),
        body={"name": "Hall E", "slug": "hall-d"},
        index=index,
        registry=default_registry(),
    )
    assert result.outcome is UpsertOutcome.UPDATED
    _, kwargs = http.patch.call_args
    # The PATCH body should contain only the differing field.
    # http.patch is called with (endpoint, dict), no kwargs in our wrapper.
    args = http.patch.call_args.args
    assert args[1] == {"name": "Hall E"}
