"""FEAT-36e tests for the categorised drop audit."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nbsnap.import_.audit import Auditor, DropCategory, DropEvent
from nbsnap.import_.driver import _record_drop
from nbsnap.import_.snapshot_index import SnapshotIndex


def _event(
    category: DropCategory = DropCategory.OUT_OF_SCOPE,
    child_ct: str = "dcim.site",
    field_name: str = "region",
    target_ct: str = "dcim.region",
    target_nk: tuple = ("elmia",),
) -> DropEvent:
    return DropEvent(
        category=category,
        child_content_type=child_ct,
        child_nk=("hall-d",),
        field_name=field_name,
        target_content_type=target_ct,
        target_nk=target_nk,
    )


# ---------------------------------------------------------------------------
# Auditor.record dedupes on the quadruple key
# ---------------------------------------------------------------------------


def test_record_deduplicates_on_quadruple_key() -> None:
    """Repeated identical drops collapse to one entry."""

    a = Auditor()
    ev = _event()
    a.record(ev)
    a.record(ev)
    a.record(ev)
    assert len(a.events) == 1


def test_record_keeps_distinct_targets_separate() -> None:
    """Two drops differing only by target NK both land."""

    a = Auditor()
    a.record(_event(target_nk=("a",)))
    a.record(_event(target_nk=("b",)))
    assert len(a.events) == 2


def test_record_keeps_distinct_fields_separate() -> None:
    """Two drops differing only by field name both land."""

    a = Auditor()
    a.record(_event(field_name="region"))
    a.record(_event(field_name="group"))
    assert len(a.events) == 2


def test_deferred_dedupe_matches_phase2_queue_key() -> None:
    """BUG-02: DEFERRED_TO_PHASE2 dedup key matches the Phase-2
    queue's `(child_ct, child_nk, field)`. Two distinct devices
    deferring the same primary_ip4 stay as TWO audit events, so
    audit count matches Phase-2 patched count."""

    def _defer(child_nk: tuple, target_nk: tuple) -> DropEvent:
        return DropEvent(
            category=DropCategory.DEFERRED_TO_PHASE2,
            child_content_type="dcim.device",
            child_nk=child_nk,
            field_name="primary_ip4",
            target_content_type="ipam.ipaddress",
            target_nk=target_nk,
        )

    a = Auditor()
    # Two different devices referencing the same IP (shared
    # primary_ip4 is unusual but the dedup must not collapse them).
    a.record(_defer(child_nk=(("h",), "d1"), target_nk=("10.0.0.1/24",)))
    a.record(_defer(child_nk=(("h",), "d2"), target_nk=("10.0.0.1/24",)))
    assert len(a.events) == 2


def test_deferred_dedupe_collapses_same_child_same_field() -> None:
    """The same `(child_ct, child_nk, field)` appearing twice in
    the deferred path collapses to one audit event, matching the
    Phase-2 queue's behaviour."""

    def _defer(target_nk: tuple) -> DropEvent:
        return DropEvent(
            category=DropCategory.DEFERRED_TO_PHASE2,
            child_content_type="dcim.device",
            child_nk=(("h",), "d1"),
            field_name="primary_ip4",
            target_content_type="ipam.ipaddress",
            target_nk=target_nk,
        )

    a = Auditor()
    a.record(_defer(target_nk=("10.0.0.1/24",)))
    # Same child + same field, even with a different target,
    # must collapse because Phase-2 only patches once per
    # (child_ct, child_nk, field).
    a.record(_defer(target_nk=("10.0.0.2/24",)))
    assert len(a.events) == 1


def test_non_deferred_categories_keep_quadruple_dedup() -> None:
    """OUT_OF_SCOPE and MISSING_FROM_SOURCE still dedupe on the
    `(child_ct, field, target_ct, target_nk)` quadruple, so a
    thousand Devices referencing the same missing Region remain
    one audit event."""

    def _missing(child_nk: tuple, target_nk: tuple) -> DropEvent:
        return DropEvent(
            category=DropCategory.MISSING_FROM_SOURCE,
            child_content_type="dcim.device",
            child_nk=child_nk,
            field_name="site",
            target_content_type="dcim.site",
            target_nk=target_nk,
        )

    a = Auditor()
    a.record(_missing(child_nk=(("h",), "d1"), target_nk=("ghost",)))
    a.record(_missing(child_nk=(("h",), "d2"), target_nk=("ghost",)))
    assert len(a.events) == 1


# ---------------------------------------------------------------------------
# Auditor.render_summary
# ---------------------------------------------------------------------------


def test_render_summary_counts_per_category() -> None:
    a = Auditor()
    a.record(_event(category=DropCategory.OUT_OF_SCOPE))
    a.record(_event(
        category=DropCategory.MISSING_FROM_SOURCE,
        child_ct="dcim.device", field_name="platform",
        target_ct="dcim.platform", target_nk=("ghost",),
    ))
    a.record(_event(
        category=DropCategory.DEFERRED_TO_PHASE2,
        child_ct="dcim.device", field_name="primary_ip4",
        target_ct="ipam.ipaddress", target_nk=("172.16.1.10/24",),
    ))
    text = a.render_summary()
    assert "out_of_scope: 1" in text
    assert "missing_from_source: 1" in text
    assert "deferred_to_phase2: 1" in text


def test_render_summary_when_no_events() -> None:
    """No events produces a friendly "nothing dropped" line."""

    assert "no FK drops" in Auditor().render_summary()


def test_render_summary_lists_top_offenders() -> None:
    """The top-offending (content_type, field) list shows
    the most frequent pairs first."""

    a = Auditor()
    for i in range(7):
        a.record(_event(target_nk=(f"region-{i}",)))
    # All seven entries share (dcim.site, region) and are
    # distinct under target_nk.
    text = a.render_summary()
    assert "dcim.site.region: 7" in text


def test_render_summary_caps_at_limit_with_trailer() -> None:
    """FEAT-48: when more than `limit` distinct (ct, field)
    pairs exist, the rendered summary shows the top `limit`
    and trails with `... and N more`."""

    a = Auditor()
    # 15 distinct (ct, field) pairs so the cap kicks in.
    for i in range(15):
        a.record(_event(
            child_ct=f"dcim.thing{i}",
            field_name="field",
            target_nk=(f"t-{i}",),
        ))
    text = a.render_summary(limit=10)
    pair_lines = [
        line for line in text.splitlines()
        if line.strip().startswith("dcim.thing")
    ]
    assert len(pair_lines) == 10
    assert "and 5 more" in text


def test_render_summary_no_trailer_when_under_limit() -> None:
    """With fewer pairs than the limit, no trailer fires."""

    a = Auditor()
    for i in range(3):
        a.record(_event(
            child_ct=f"dcim.thing{i}",
            field_name="field",
            target_nk=(f"t-{i}",),
        ))
    text = a.render_summary(limit=10)
    assert "more (see audit log)" not in text


# ---------------------------------------------------------------------------
# Auditor.write_jsonl roundtrip
# ---------------------------------------------------------------------------


def test_write_jsonl_emits_one_object_per_line(tmp_path: Path) -> None:
    """Each event becomes one JSON object on its own line."""

    a = Auditor()
    a.record(_event())
    a.record(_event(
        category=DropCategory.MISSING_FROM_SOURCE,
        child_ct="dcim.device", field_name="platform",
        target_ct="dcim.platform", target_nk=("ghost",),
    ))
    path = tmp_path / "audit.jsonl"
    a.write_jsonl(path)

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert rows[0]["category"] == "out_of_scope"
    assert rows[0]["target"] == {"content_type": "dcim.region", "nk": ["elmia"]}
    assert rows[1]["category"] == "missing_from_source"


def test_write_jsonl_creates_parent_directories(tmp_path: Path) -> None:
    """If the audit path's parent does not exist, it is created
    so the operator does not have to mkdir first."""

    path = tmp_path / "nested" / "subdir" / "audit.jsonl"
    Auditor().write_jsonl(path)
    assert path.exists()


def test_write_jsonl_handles_empty_event_list(tmp_path: Path) -> None:
    """An empty audit still produces an empty file so downstream
    tooling does not have to special-case "no log"."""

    path = tmp_path / "audit.jsonl"
    Auditor().write_jsonl(path)
    assert path.exists()
    assert path.read_text() == ""


# ---------------------------------------------------------------------------
# _record_drop classifier (driver-internal)
# ---------------------------------------------------------------------------


@pytest.fixture()
def snapshot_with_region() -> SnapshotIndex:
    """A snapshot that covers dcim.region but is missing the
    specific NK ('elmia',). Used for the MISSING_FROM_SOURCE
    branch."""

    idx = SnapshotIndex()
    idx._by_key[("dcim.region", ("other",))] = {"name": "Other"}
    return idx


@pytest.fixture()
def snapshot_without_region() -> SnapshotIndex:
    """A snapshot that does not cover dcim.region at all. Used
    for the OUT_OF_SCOPE branch."""

    idx = SnapshotIndex()
    idx._by_key[("dcim.site", ("hall-d",))] = {"name": "Hall-D"}
    return idx


def test_record_drop_classifies_out_of_scope(
    snapshot_without_region: SnapshotIndex,
) -> None:
    """Target content type is not in the snapshot at all."""

    auditor = Auditor()
    _record_drop(
        auditor=auditor,
        snapshot_index=snapshot_without_region,
        deferred_queue=[],
        queue_size_before=0,
        value=["elmia"],
        child_ct="dcim.site",
        child_nk=(("hall-d",),),
        field_name="region",
        target_ct="dcim.region",
    )
    assert auditor.events[0].category is DropCategory.OUT_OF_SCOPE


def test_record_drop_classifies_missing_from_source(
    snapshot_with_region: SnapshotIndex,
) -> None:
    """Target CT is in the snapshot but this NK is not."""

    auditor = Auditor()
    _record_drop(
        auditor=auditor,
        snapshot_index=snapshot_with_region,
        deferred_queue=[],
        queue_size_before=0,
        value=["elmia"],
        child_ct="dcim.site",
        child_nk=(("hall-d",),),
        field_name="region",
        target_ct="dcim.region",
    )
    assert auditor.events[0].category is DropCategory.MISSING_FROM_SOURCE


def test_record_drop_classifies_deferred_to_phase2() -> None:
    """The look-ahead pushed an entry on the deferred queue
    while we were resolving this field, so the cycle-breaker is
    handling it; classify as DEFERRED_TO_PHASE2."""

    auditor = Auditor()
    queue: list = [MagicMock()]  # simulates a pre-existing entry
    _record_drop(
        auditor=auditor,
        snapshot_index=SnapshotIndex(),
        deferred_queue=queue + [MagicMock()],  # grew by one
        queue_size_before=len(queue),
        value=["10.0.0.1/24"],
        child_ct="dcim.device",
        child_nk=(("h",), "d"),
        field_name="primary_ip4",
        target_ct="ipam.ipaddress",
    )
    assert auditor.events[0].category is DropCategory.DEFERRED_TO_PHASE2


def test_record_drop_is_noop_when_auditor_is_none() -> None:
    """Backwards-compat: a caller that does not pass an auditor
    sees no error and no recording."""

    # Should not raise.
    _record_drop(
        auditor=None,
        snapshot_index=None,
        deferred_queue=None,
        queue_size_before=0,
        value=("x",),
        child_ct="dcim.site",
        child_nk=(),
        field_name="region",
        target_ct="dcim.region",
    )


def test_snapshot_index_has_content_type() -> None:
    """`has_content_type` returns True only when at least one
    row for the CT is in the index."""

    idx = SnapshotIndex()
    idx._by_key[("dcim.site", ("hall-d",))] = {}
    assert idx.has_content_type("dcim.site") is True
    assert idx.has_content_type("dcim.region") is False


def test_bypass_coerced_emits_one_event_per_field() -> None:
    """BUG-01b: when the import-side coerce collapses an
    enum-dict field on the way to a POST, the auditor records
    a BYPASS_COERCED event so the operator can grep the audit
    log and see which records were touched."""

    from unittest.mock import MagicMock

    from nbsnap.import_.upsert import UpsertOutcome, upsert

    http = MagicMock()
    http.base_url = "https://dest.example/"
    http._cf_cache = None
    http._cf_cache_failed = False
    http.get_all.side_effect = lambda endpoint: iter([])
    http.post.return_value = {"id": 42}

    index = MagicMock()
    index.ensure_built = MagicMock()
    index.lookup.return_value = None
    index.upsert_mapping = {}

    auditor = Auditor()
    upsert(
        http,
        content_type="dcim.site",
        natural_key=("hall-a",),
        body={
            "slug": "hall-a",
            "name": "Hall-A",
            "status": {"value": "active", "label": "Active"},
        },
        index=index,
        registry=MagicMock(),
        auditor=auditor,
    )
    bypass = [
        ev for ev in auditor.events
        if ev.category is DropCategory.BYPASS_COERCED
    ]
    assert len(bypass) == 1
    assert bypass[0].field_name == "status"
    assert bypass[0].child_content_type == "dcim.site"
