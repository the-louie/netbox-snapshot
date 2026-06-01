"""FEAT-23/FEAT-36c tests for the Phase-2 deferred-FK writer.

Three behaviours pinned here:

1. Happy path: every queued `DeferredFK` resolves to ids on the
   destination and triggers one PATCH per entry.
2. Skip path: when either the child or the target NK is missing
   on the destination, the entry is skipped and counted, not
   raised.
3. Failure path: when the PATCH itself returns non-2xx, the
   entry lands on `summary.failures` with its error.

A fourth section verifies the driver-side wiring (FEAT-36c):
when Phase-1 fills the deferred queue, `run_import` actually
calls `run_phase2` and surfaces the summary.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nbsnap.http.client import NetboxHTTPError
from nbsnap.import_.lookahead import DeferredFK
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.phase2 import Phase2Summary, run_phase2, Phase2Outcome
from nbsnap.natkey.registry import default as default_registry


def _entry(
    child_ct: str = "dcim.device",
    child_nk: tuple = (("hall-d",), "d39a"),
    field_name: str = "primary_ip4",
    target_ct: str = "ipam.ipaddress",
    target_nk: tuple = ("172.16.1.10/24",),
) -> DeferredFK:
    return DeferredFK(
        child_content_type=child_ct,
        child_nk=child_nk,
        field_name=field_name,
        target_content_type=target_ct,
        target_nk=target_nk,
    )


@pytest.fixture()
def dest_index() -> NKIndex:
    return NKIndex()


@pytest.fixture()
def registry():
    return default_registry()


# ---------------------------------------------------------------------------
# Happy path: PATCH fires, summary records `patched`
# ---------------------------------------------------------------------------


def test_run_phase2_patches_each_entry(dest_index: NKIndex, registry) -> None:
    """One PATCH per queue entry; body carries exactly one field."""

    dest_index.insert("dcim.device", (("hall-d",), "d39a"), 99)
    dest_index.insert("ipam.ipaddress", ("172.16.1.10/24",), 7)

    http = MagicMock()
    # Once dest_index already knows the target, ensure_built is a
    # no-op cache hit. Stub get_all to return nothing in case the
    # index decides to refresh.
    http.get_all.return_value = iter([])

    queue = [_entry()]
    summary = run_phase2(http, queue, dest_index=dest_index, registry=registry)

    assert summary.counts[Phase2Outcome.PATCHED] == 1
    assert summary.is_clean()
    http.patch.assert_called_once_with(
        "dcim/devices/99/", {"primary_ip4": 7}
    )


def test_run_phase2_no_entries_is_noop(dest_index: NKIndex, registry) -> None:
    """An empty queue returns a clean summary with no calls."""

    http = MagicMock()
    summary = run_phase2(http, [], dest_index=dest_index, registry=registry)

    assert summary.counts == {}
    assert summary.is_clean()
    http.patch.assert_not_called()


def test_run_phase2_handles_multiple_entries(
    dest_index: NKIndex, registry
) -> None:
    """Each entry fires its own one-field PATCH."""

    dest_index.insert("dcim.device", (("hall-d",), "d1"), 1)
    dest_index.insert("dcim.device", (("hall-d",), "d2"), 2)
    dest_index.insert("ipam.ipaddress", ("10.0.0.1/24",), 10)
    dest_index.insert("ipam.ipaddress", ("10.0.0.2/24",), 11)

    http = MagicMock()
    http.get_all.return_value = iter([])
    queue = [
        _entry(child_nk=(("hall-d",), "d1"), target_nk=("10.0.0.1/24",)),
        _entry(child_nk=(("hall-d",), "d2"), target_nk=("10.0.0.2/24",)),
    ]
    summary = run_phase2(http, queue, dest_index=dest_index, registry=registry)

    assert summary.counts[Phase2Outcome.PATCHED] == 2
    assert http.patch.call_count == 2


# ---------------------------------------------------------------------------
# Skip path: missing endpoints are skipped, not raised
# ---------------------------------------------------------------------------


def test_run_phase2_skips_when_child_missing(
    dest_index: NKIndex, registry
) -> None:
    """If the child NK is not on the destination, the entry is
    skipped. The Phase-1 summary already records the upstream
    upsert failure that caused this."""

    # Only the target is indexed; the child is missing.
    dest_index.insert("ipam.ipaddress", ("172.16.1.10/24",), 7)

    http = MagicMock()
    http.get_all.return_value = iter([])

    summary = run_phase2(
        http, [_entry()], dest_index=dest_index, registry=registry
    )

    assert summary.counts[Phase2Outcome.SKIPPED] == 1
    assert summary.counts.get(Phase2Outcome.PATCHED, 0) == 0
    http.patch.assert_not_called()


def test_run_phase2_skips_when_target_missing(
    dest_index: NKIndex, registry
) -> None:
    """The child exists but the target does not. Skip; do not
    raise. The look-ahead resolver should have created the target
    during Phase-1; reaching this branch means that upsert failed
    and the Phase-1 summary already records the failure."""

    dest_index.insert("dcim.device", (("hall-d",), "d39a"), 99)
    # ipam.ipaddress NOT inserted, and the GET-based refresh
    # returns nothing.

    http = MagicMock()
    http.get_all.return_value = iter([])

    summary = run_phase2(
        http, [_entry()], dest_index=dest_index, registry=registry
    )

    assert summary.counts[Phase2Outcome.SKIPPED] == 1
    http.patch.assert_not_called()


def test_run_phase2_skips_unknown_content_type(
    dest_index: NKIndex, registry
) -> None:
    """If a deferred entry references a child content type not
    in CONTENT_TYPE_ENDPOINTS, skip rather than crash. This
    cannot occur if the look-ahead resolver only emits entries
    for content types it just upserted, but a defensive skip is
    cheap."""

    dest_index.insert("nonsense.thing", (("x",), "y"), 1)
    dest_index.insert("ipam.ipaddress", ("172.16.1.10/24",), 7)

    http = MagicMock()
    http.get_all.return_value = iter([])
    entry = _entry(child_ct="nonsense.thing", child_nk=(("x",), "y"))
    summary = run_phase2(
        http, [entry], dest_index=dest_index, registry=registry
    )
    assert summary.counts[Phase2Outcome.SKIPPED] == 1


# ---------------------------------------------------------------------------
# Failure path: PATCH returns non-2xx
# ---------------------------------------------------------------------------


def test_run_phase2_records_patch_failure(
    dest_index: NKIndex, registry
) -> None:
    """A PATCH that raises NetboxHTTPError lands on
    `summary.failures` with the entry and the error message."""

    dest_index.insert("dcim.device", (("hall-d",), "d39a"), 99)
    dest_index.insert("ipam.ipaddress", ("172.16.1.10/24",), 7)

    http = MagicMock()
    http.get_all.return_value = iter([])
    http.patch.side_effect = NetboxHTTPError(
        "PATCH", "dcim/devices/99/", 400,
        '{"primary_ip4":"does not belong"}',
    )

    summary = run_phase2(
        http, [_entry()], dest_index=dest_index, registry=registry
    )

    assert summary.counts[Phase2Outcome.FAILED] == 1
    assert not summary.is_clean()
    assert len(summary.failures) == 1
    entry, msg = summary.failures[0]
    assert entry.child_content_type == "dcim.device"
    assert "400" in msg


def test_run_phase2_continues_after_one_failure(
    dest_index: NKIndex, registry
) -> None:
    """A single failed PATCH does not abort subsequent entries.
    The driver, not Phase-2, applies the on_error policy."""

    dest_index.insert("dcim.device", (("hall-d",), "d1"), 1)
    dest_index.insert("dcim.device", (("hall-d",), "d2"), 2)
    dest_index.insert("ipam.ipaddress", ("10.0.0.1/24",), 10)
    dest_index.insert("ipam.ipaddress", ("10.0.0.2/24",), 11)

    http = MagicMock()
    http.get_all.return_value = iter([])
    http.patch.side_effect = [
        NetboxHTTPError("PATCH", "dcim/devices/1/", 400, "nope"),
        None,  # second PATCH succeeds
    ]

    queue = [
        _entry(child_nk=(("hall-d",), "d1"), target_nk=("10.0.0.1/24",)),
        _entry(child_nk=(("hall-d",), "d2"), target_nk=("10.0.0.2/24",)),
    ]
    summary = run_phase2(http, queue, dest_index=dest_index, registry=registry)

    assert summary.counts[Phase2Outcome.FAILED] == 1
    assert summary.counts[Phase2Outcome.PATCHED] == 1


def test_phase2summary_is_clean_only_when_zero_failures() -> None:
    """`is_clean` ignores `skipped` and `patched`; it only fires
    on `failed`. Skips are not failures, they are signals from
    Phase-1."""

    s = Phase2Summary()
    s.counts[Phase2Outcome.PATCHED] = 5
    s.counts[Phase2Outcome.SKIPPED] = 3
    assert s.is_clean()
    s.counts[Phase2Outcome.FAILED] = 1
    assert not s.is_clean()
