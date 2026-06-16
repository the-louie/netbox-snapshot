"""ARCH-09d: resolver-error attributes project cleanly into audit rows.

The full integration version of this test would drive a snapshot
through ``run_import`` and inspect ``audit.jsonl``, but the
destination-stack-free version verifies the cheaper contract:
:class:`ResolverFieldError` and :class:`ResolverFKMissError` expose
exactly the attributes a :class:`DropEvent` needs to anchor an
audit row to a content type, a natural key, and a field name.

If a future refactor renames or removes one of those attributes,
the next ``except`` clause in the importer would silently swallow
context and the audit log would lose the anchor; this test fails
loudly.
"""

from __future__ import annotations

from nbsnap.import_.audit import DropCategory, DropEvent
from nbsnap.import_.fk_resolve import ResolverFKMissError
from nbsnap.natkey.resolver import ResolverFieldError


def test_resolver_field_error_supplies_audit_anchors() -> None:
    err = ResolverFieldError(
        "field empty",
        content_type="dcim.device",
        natural_key=(("c",), "d39a"),
        field_name="primary_ip4",
        hint="missing source data",
    )

    # The importer projects the four anchor attributes into a
    # DropEvent. We do that manually here to pin the projection.
    event = DropEvent(
        category=DropCategory.MISSING_FROM_SOURCE,
        child_content_type=err.content_type,
        child_nk=err.natural_key or (),
        field_name=err.field_name,
        target_content_type="",  # not known at the field-level raise
        target_nk=(),
        message=str(err),
    )

    row = event.to_json()
    assert row["child"]["content_type"] == "dcim.device"
    # DropEvent.to_json shallow-converts the outer NK to a list;
    # inner tuples stay tuples in memory but json.dumps still emits
    # them as JSON arrays on disk.
    assert row["child"]["nk"] == [("c",), "d39a"]
    assert row["field"] == "primary_ip4"
    assert "missing source data" in row["message"]


def test_resolver_fk_miss_error_supplies_audit_anchors() -> None:
    err = ResolverFKMissError(
        "NK not found on destination",
        content_type="dcim.interface",
        natural_key=(("c",), "C-ESPORTS", "ge-0/0/8"),
        target_ct="ipam.ipaddress",
        hint="missing source data",
    )

    event = DropEvent(
        category=DropCategory.UPSERT_FAILED,
        child_content_type=err.content_type,
        child_nk=err.natural_key or (),
        field_name="primary_ip4",  # caller supplies this
        target_content_type=err.target_ct,
        target_nk=(),
        message=str(err),
    )

    row = event.to_json()
    assert row["child"]["content_type"] == "dcim.interface"
    assert row["target"]["content_type"] == "ipam.ipaddress"
    assert "missing source data" in row["message"]
