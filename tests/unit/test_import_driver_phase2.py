"""FEAT-36c driver-wiring tests for Phase-2.

Pins the contract between `run_import` and `run_phase2`:

1. When Phase-1 collects deferred FKs, `run_phase2` is called
   with that queue.
2. The returned `Phase2Summary` is surfaced on `ImportSummary`.
3. Under `on_error="stop"`, a Phase-2 failure aborts before
   returning; the summary still carries the partial result.
4. An empty deferred queue means no Phase-2 call at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from nbsnap.import_.driver import ImportSummary, run_import
from nbsnap.import_.lookahead import DeferredFK
from nbsnap.import_.phase2 import Phase2Summary, Phase2Outcome
from nbsnap.schema.status import VersionSkew


def _write_min_snapshot(snapshot_dir: Path) -> None:
    """Write the smallest possible snapshot that the driver can
    load: a manifest with no counts and an empty OpenAPI schema."""

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    # Manifest carrying a netbox_version so preflight can compare.
    manifest = {
        "version": 1,
        "netbox_version": "4.6.2",
        "counts": {},
        "deferred_edges": [],
    }
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest))
    # OpenAPI schema at the conventional path. The driver loads
    # this for FK resolution; an empty paths block keeps the
    # validator happy.
    schema_dir = snapshot_dir / "schema"
    schema_dir.mkdir(exist_ok=True)
    (schema_dir / "openapi.json").write_text(json.dumps({
        "openapi": "3.0.3", "paths": {}, "components": {"schemas": {}}
    }))


def _stub_preflight(monkeypatch, *, blocking: bool = False) -> None:
    """Bypass the real preflight against an empty-mock NetBox."""

    fake_report = MagicMock()
    fake_report.is_blocking.return_value = blocking
    monkeypatch.setattr(
        "nbsnap.import_.driver.run_preflight", lambda *_a, **_k: fake_report
    )


def test_driver_calls_phase2_when_queue_nonempty(
    tmp_path: Path, monkeypatch
) -> None:
    """If the look-ahead path filled the deferred queue, the
    driver invokes `run_phase2` with that queue."""

    _write_min_snapshot(tmp_path)
    _stub_preflight(monkeypatch)

    # Inject a deferred entry by patching the look-ahead module
    # so the driver thinks Phase-1 produced one. We do this by
    # patching `run_phase2` directly and asserting the call.
    captured: dict = {}

    def fake_phase2(http, queue, *, dest_index, registry):  # noqa: ARG001
        captured["queue"] = list(queue)
        return Phase2Summary()

    # Pre-load a deferred entry into the queue by patching the
    # SnapshotIndex.from_snapshot return so the driver picks up
    # an empty snapshot but we still inject into deferred_queue
    # via a sneaky monkeypatch on run_phase2's container. The
    # cleanest approach is to patch run_phase2 plus inject via
    # _resolve_body's deferred_queue hook. Easier: assert that
    # given an explicit non-empty queue (constructed in-test) the
    # driver code-path fires.
    #
    # We do this by writing a content type file the driver iterates
    # then patching `_resolve_body` to drop a DeferredFK onto the
    # queue argument. That matches the real Phase-1 contract.
    entry = DeferredFK(
        child_content_type="dcim.device",
        child_nk=(("h",), "d"),
        field_name="primary_ip4",
        target_content_type="ipam.ipaddress",
        target_nk=("10.0.0.1/24",),
    )

    def fake_resolve_body(*_a, **kw):
        kw["deferred_queue"].append(entry)
        return {}

    # The driver still needs at least one content type to iterate
    # so _resolve_body gets called. Write a single jsonl row.
    dev_dir = tmp_path / "dcim"
    dev_dir.mkdir(exist_ok=True)
    (dev_dir / "devices.jsonl").write_text(json.dumps({
        "natural_key": [["h"], "d"], "body": {"name": "d"}
    }) + "\n")
    # And surface dcim.device in the manifest counts so the
    # planner walks it.
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["counts"] = {"dcim.device": 1}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    fake_upsert = MagicMock()
    fake_upsert.return_value = MagicMock(outcome=MagicMock(name="CREATED"))
    fake_upsert.return_value.outcome.__eq__ = lambda _self, _other: False

    with patch("nbsnap.import_.driver._resolve_body", fake_resolve_body), \
         patch("nbsnap.import_.driver.upsert", fake_upsert), \
         patch("nbsnap.import_.phase2.run_phase2", side_effect=fake_phase2):
        summary = run_import(
            MagicMock(), tmp_path,
            max_skew=VersionSkew.MAJOR, on_error="continue",
        )

    assert isinstance(summary, ImportSummary)
    # Phase-2 was called with the entry produced during Phase-1.
    assert "queue" in captured
    assert captured["queue"] == [entry]


def test_driver_skips_phase2_when_queue_empty(
    tmp_path: Path, monkeypatch
) -> None:
    """No deferred entries means no Phase-2 call. `summary.phase2`
    stays None."""

    _write_min_snapshot(tmp_path)
    _stub_preflight(monkeypatch)

    called = MagicMock()
    with patch("nbsnap.import_.phase2.run_phase2", called):
        summary = run_import(
            MagicMock(), tmp_path,
            max_skew=VersionSkew.MAJOR, on_error="continue",
        )

    called.assert_not_called()
    assert summary.phase2 is None


def test_driver_stop_on_phase2_failure(tmp_path: Path, monkeypatch) -> None:
    """`on_error=stop` returns after a Phase-2 failure with the
    partial summary intact."""

    _write_min_snapshot(tmp_path)
    _stub_preflight(monkeypatch)

    # Drive the same one-row-with-deferral setup as the happy
    # path test, but make Phase-2 report a failure.
    entry = DeferredFK(
        child_content_type="dcim.device",
        child_nk=(("h",), "d"),
        field_name="primary_ip4",
        target_content_type="ipam.ipaddress",
        target_nk=("10.0.0.1/24",),
    )
    dev_dir = tmp_path / "dcim"
    dev_dir.mkdir(exist_ok=True)
    (dev_dir / "devices.jsonl").write_text(json.dumps({
        "natural_key": [["h"], "d"], "body": {"name": "d"}
    }) + "\n")
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["counts"] = {"dcim.device": 1}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    def fake_resolve_body(*_a, **kw):
        kw["deferred_queue"].append(entry)
        return {}

    failed_summary = Phase2Summary()
    failed_summary.counts[Phase2Outcome.FAILED] = 1
    failed_summary.failures.append((entry, "HTTP 400 bad"))

    with patch("nbsnap.import_.driver._resolve_body", fake_resolve_body), \
         patch("nbsnap.import_.driver.upsert") as fake_upsert, \
         patch("nbsnap.import_.phase2.run_phase2", return_value=failed_summary):
        # Make Phase-1 succeed so we reach Phase-2.
        from nbsnap.import_.upsert import UpsertOutcome, UpsertResult
        fake_upsert.return_value = UpsertResult(
            outcome=UpsertOutcome.CREATED,
            content_type="dcim.device",
            natural_key=(("h",), "d"),
            destination_id=1,
        )
        summary = run_import(
            MagicMock(), tmp_path,
            max_skew=VersionSkew.MAJOR, on_error="stop",
        )

    assert summary.phase2 is failed_summary
    assert not summary.phase2.is_clean()
