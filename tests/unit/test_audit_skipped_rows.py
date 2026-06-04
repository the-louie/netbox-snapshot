"""BUG-13: SKIPPED upserts must emit one audit row per skipped record.

Before this change the audit JSONL carried only FK drops
(OUT_OF_SCOPE, MISSING_FROM_SOURCE, DEFERRED_TO_PHASE2,
UPSERT_FAILED*, BYPASS_COERCED). The SKIPPED bucket was visible
only in the textual summary, so the rescue loop could not
attribute a skip back to a specific row's NK.

These tests pin two behaviours:

* The auditor accepts `category=SKIPPED` events with per-row
  `(child_ct, child_nk)` dedup, so two distinct skipped rows
  produce two distinct audit lines, but the same row processed
  twice (e.g. via look-ahead then the main phase) only counts once.
* The summary count and the audit count agree.
"""

from __future__ import annotations

from nbsnap.import_.audit import Auditor, DropCategory, DropEvent


def _skipped(child_ct: str, child_nk: tuple, message: str = "") -> DropEvent:
    return DropEvent(
        category=DropCategory.SKIPPED,
        child_content_type=child_ct,
        child_nk=child_nk,
        field_name="",
        target_content_type="",
        target_nk=(),
        message=message,
    )


def test_skipped_distinct_rows_each_emit_one_audit_line() -> None:
    """Two different ipam.ipaddress NKs skipped for duplicate reason
    must produce two audit entries, not collapse to one."""

    a = Auditor()
    a.record(_skipped(
        "ipam.ipaddress",
        ("172.16.255.5/32", "dcim.interface", ((("d",), "D-MIRAGE-PALACE-SW"), "lo0.0")),
        message="ip-address refused due to a duplicate already on the destination.",
    ))
    a.record(_skipped(
        "ipam.ipaddress",
        ("172.16.255.6/32", "dcim.interface", ((("d",), "D-Neon-District-SW"), "lo0.0")),
        message="ip-address refused due to a duplicate already on the destination.",
    ))
    assert len(a.events) == 2
    assert all(e.category is DropCategory.SKIPPED for e in a.events)


def test_skipped_same_row_twice_dedups_to_one() -> None:
    """A row processed twice (look-ahead then main phase) must
    only count once in the audit log."""

    a = Auditor()
    ev = _skipped("ipam.iprange", ("10.0.0.1", "10.0.0.10"), message="iprange overlap")
    a.record(ev)
    a.record(ev)
    assert len(a.events) == 1


def test_skipped_dedup_is_independent_of_message() -> None:
    """The dedup key is (child_ct, child_nk); two records for the
    same NK with different reason strings still collapse — the
    first message wins, matching FIRST_WINS behaviour of the
    existing dedup contract."""

    a = Auditor()
    a.record(_skipped("ipam.iprange", ("10.0.0.1", "10.0.0.10"), message="overlap"))
    a.record(_skipped("ipam.iprange", ("10.0.0.1", "10.0.0.10"), message="other reason"))
    assert len(a.events) == 1
    assert a.events[0].message == "overlap"


def test_skipped_does_not_collide_with_fk_drop_dedup() -> None:
    """A SKIPPED event for `(child_ct, child_nk)` and a
    MISSING_FROM_SOURCE event for the same child must both
    survive — they live in different dedup namespaces because
    SKIPPED keys on (child_ct, child_nk) and MISSING_FROM_SOURCE
    keys on (child_ct, field, target_ct, target_nk)."""

    a = Auditor()
    a.record(_skipped("ipam.ipaddress", ("10.0.0.1/32",), message="duplicate"))
    a.record(DropEvent(
        category=DropCategory.MISSING_FROM_SOURCE,
        child_content_type="ipam.ipaddress",
        child_nk=("10.0.0.1/32",),
        field_name="assigned_object",
        target_content_type="dcim.interface",
        target_nk=(("device-a",), "eth0"),
    ))
    assert len(a.events) == 2


def test_skipped_audit_serialises_to_jsonl(tmp_path) -> None:
    """The on-disk audit JSONL must carry the SKIPPED rows so the
    rescue loop's mining pass can grep on `"category": "skipped"`."""

    import json

    a = Auditor()
    a.record(_skipped(
        "ipam.ipaddress",
        ("172.16.255.5/32",),
        message="duplicate already on destination",
    ))
    out = tmp_path / "audit.jsonl"
    a.write_jsonl(out)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == 1
    row = lines[0]
    assert row["category"] == "skipped"
    assert row["child"]["content_type"] == "ipam.ipaddress"
    assert row["child"]["nk"] == ["172.16.255.5/32"]
    assert row["message"] == "duplicate already on destination"


def test_skipped_count_appears_in_render_summary() -> None:
    """The summary block must show the SKIPPED count so the
    cross-check in import_cli has a stable source."""

    a = Auditor()
    a.record(_skipped("ipam.ipaddress", ("a",), message="dup"))
    a.record(_skipped("ipam.ipaddress", ("b",), message="dup"))
    a.record(_skipped("ipam.iprange", ("c", "d"), message="overlap"))
    out = a.render_summary()
    assert "skipped: 3" in out
