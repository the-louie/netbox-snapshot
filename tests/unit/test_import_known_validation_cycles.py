"""Tests for task #33: KNOWN_VALIDATION_CYCLES merge.

The manifest's `deferred_edges` only lists cycles the planner
can detect from the OpenAPI schema. NetBox enforces additional
write-time validation cycles that the schema does not express,
the canonical case is `dcim.device.primary_ip4`, where NetBox
refuses the create unless the referenced IPAddress's
`assigned_object` is one of THIS device's interfaces.

The curated `KNOWN_VALIDATION_CYCLES` table covers these
cases. `known_validation_cycle_fields()` returns the entries
in the `content_type -> set[field_name]` shape the driver's
`deferred_fields_by_ct` already uses, so merging is a clean
`setdefault + update` per content type.

Three behaviours pinned here:

1. The helper returns the curated entries with the expected
   `dcim.device.primary_ip4/primary_ip6/oob_ip` set under
   `dcim.device`.
2. Every entry carries `verified_against` so a NetBox version
   bump that changes these rules can be caught by re-running
   the table check against the new write validator.
3. Merging the table into a pre-existing
   `deferred_fields_by_ct` is additive, manifest-deferred
   fields plus curated fields end up in the same set.
"""

from __future__ import annotations

from nbsnap.graph.polymorphic import (
    KNOWN_VALIDATION_CYCLES,
    known_validation_cycle_fields,
)


def test_device_primary_ip4_is_in_the_curated_table() -> None:
    """The headline assertion, the field the rescue-10 import
    keeps tripping over is in the table."""

    fields = known_validation_cycle_fields()
    assert "primary_ip4" in fields.get("dcim.device", set())


def test_device_primary_ip6_and_oob_ip_are_in_the_table() -> None:
    """Same validation rule, IPv6 and out-of-band management
    variants. NetBox enforces them too, so we strip them on
    POST and Phase-2 patches them in."""

    fields = known_validation_cycle_fields()
    assert "primary_ip6" in fields["dcim.device"]
    assert "oob_ip" in fields["dcim.device"]


def test_every_entry_carries_verified_against() -> None:
    """A NetBox version bump that changes these validators
    should surface as the curated table going stale. The
    `verified_against` tag makes it easy to grep for entries
    that need re-verification."""

    for entry in KNOWN_VALIDATION_CYCLES:
        assert "verified_against" in entry
        assert "netbox" in entry["verified_against"].lower()


def test_helper_shape_matches_driver_expectation() -> None:
    """The driver merges the helper's return value into
    `deferred_fields_by_ct: dict[str, set[str]]`. Confirm the
    helper produces exactly that shape so the merge is a
    drop-in `setdefault + update`."""

    fields = known_validation_cycle_fields()
    assert isinstance(fields, dict)
    for ct, field_set in fields.items():
        assert isinstance(ct, str)
        assert isinstance(field_set, set)
        for name in field_set:
            assert isinstance(name, str)


def test_merge_is_additive_with_manifest_deferred_edges() -> None:
    """Simulate the driver's merge: start with a manifest-style
    deferred index, merge the curated table, assert both sources
    survive. This is the exact code path that lives at the top
    of `run_import`."""

    # Manifest-source entries (the planner-marked deferrals).
    deferred_fields_by_ct: dict[str, set[str]] = {
        "dcim.device": {"parent_device"},  # the existing planner entry
        "ipam.ipaddress": {"nat_inside", "nat_outside"},
    }
    # Driver-side merge (mirrors the code in run_import).
    for ct, fields in known_validation_cycle_fields().items():
        deferred_fields_by_ct.setdefault(ct, set()).update(fields)

    # Both sources survived on dcim.device.
    device_fields = deferred_fields_by_ct["dcim.device"]
    assert "parent_device" in device_fields  # manifest-source
    assert "primary_ip4" in device_fields  # curated-source
    assert "primary_ip6" in device_fields
    assert "oob_ip" in device_fields

    # Manifest-only content types unchanged.
    assert deferred_fields_by_ct["ipam.ipaddress"] == {"nat_inside", "nat_outside"}


def test_table_does_not_contain_planner_detected_fields() -> None:
    """The curated table should NOT duplicate self-loop
    cycles the planner already finds (dcim.devicerole.parent
    etc.). Adding entries that the planner already detects
    is harmless thanks to the set-union merge, but it's worth
    flagging if anyone slips a duplicate in."""

    fields = known_validation_cycle_fields()
    # These are planner-detected self-loops, they should NOT
    # appear in the curated table.
    assert "parent" not in fields.get("dcim.devicerole", set())
    assert "parent" not in fields.get("dcim.platform", set())
    assert "qinq_svlan" not in fields.get("ipam.vlan", set())
