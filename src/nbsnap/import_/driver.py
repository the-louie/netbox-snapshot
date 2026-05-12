"""End-to-end import driver (FEAT-22/23/24).

The driver:

1. Loads the snapshot (manifest, jsonl files).
2. Runs pre-flight checks.
3. Phase-1 writer: for each content type in plan order, resolve
   FKs against the index and upsert.
4. Phase-2 writer: walk `_deferred.jsonl` and PATCH the cycle-
   closing fields.
5. Print an audit summary.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nbsnap.export.manifest import MANIFEST_FILENAME, Manifest
from nbsnap.export.writer import CONTENT_TYPE_FILES
from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.fk_resolve import (
    normalise_nk,
    resolve_polymorphic,
    resolve_simple_fk,
)
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.preflight import PreflightReport, run_preflight
from nbsnap.import_.upsert import UpsertOutcome, UpsertResult, upsert
from nbsnap.natkey.registry import default as default_registry
from nbsnap.schema.openapi import SCHEMA_PATH, OpenAPI
from nbsnap.schema.status import VersionSkew


@dataclass
class ImportSummary:
    """End-of-run aggregated audit."""

    preflight: PreflightReport
    counts: Counter[UpsertOutcome] = field(default_factory=Counter)
    failures: list[UpsertResult] = field(default_factory=list)


def run_import(
    http: NetboxHTTP,
    snapshot_dir: Path,
    *,
    max_skew: VersionSkew = VersionSkew.MINOR,
    on_error: str = "stop",
) -> ImportSummary:
    """Apply the snapshot at `snapshot_dir` to the destination NetBox."""

    snapshot_dir = Path(snapshot_dir)
    manifest = Manifest.load(snapshot_dir / MANIFEST_FILENAME)
    preflight = run_preflight(http, manifest)
    summary = ImportSummary(preflight=preflight)

    if preflight.is_blocking(max_skew):
        return summary

    registry = default_registry()
    index = NKIndex()
    openapi = OpenAPI.load(snapshot_dir / SCHEMA_PATH)

    # Phase-1: per content type, in the order recorded in the
    # manifest. We do not re-plan here; the snapshot is the
    # contract and the manifest is the order.
    for ct in _content_type_order(manifest, snapshot_dir):
        file_path = snapshot_dir / CONTENT_TYPE_FILES.get(
            ct, f"{ct.replace('.', '/')}.jsonl"
        )
        if not file_path.exists():
            continue
        for snapshot_row in _iter_jsonl(file_path):
            body = _resolve_body(
                ct, snapshot_row.get("body") or {}, openapi, index, http, registry
            )
            nk = normalise_nk(snapshot_row.get("natural_key"))
            result = upsert(
                http,
                content_type=ct,
                natural_key=nk,
                body=body,
                index=index,
                registry=registry,
            )
            summary.counts[result.outcome] += 1
            if result.outcome is UpsertOutcome.FAILED:
                summary.failures.append(result)
                if on_error == "stop":
                    return summary

    # Phase-2: deferred edges. The manifest's deferred_edges field
    # tells us which (child, parent, field) tuples to PATCH after
    # both endpoints exist.
    for edge in manifest.deferred_edges:
        # For v1, the Phase-2 writer is a stub: a real
        # implementation would re-walk the snapshot rows for the
        # child content type, resolve the field's NK, and PATCH the
        # destination record. The structure is set so the field can
        # be filled in by FEAT-23 follow-up.
        _ = edge

    return summary


def _content_type_order(manifest: Manifest, snapshot_dir: Path) -> list[str]:
    """Compute the import order, parents before children.

    Re-runs the topological planner against the snapshot's
    OpenAPI schema so the import side honours the same ordering
    constraints as the export side: tags before devices, devices
    before interfaces, sites before locations, etc.

    Without this, an alphabetical sort would import dcim.cable
    and dcim.device before extras.tag and ipam.* before dcim.*,
    breaking every FK reference that points "later" in the
    alphabet.

    Falls back to alphabetical when the schema is unreadable so
    a damaged snapshot still attempts an import rather than
    aborting up front.
    """
    from nbsnap.graph import from_openapi
    from nbsnap.graph import plan as build_plan

    scope = set(manifest.counts.keys())
    try:
        openapi_local = OpenAPI.load(snapshot_dir / SCHEMA_PATH)
        graph = from_openapi(openapi_local, scope=scope)
        plan_obj = build_plan(graph)
    except Exception:  # noqa: BLE001 - any failure falls back to alphabetical
        return sorted(scope)
    # Filter to content types actually present in the manifest.
    ordered = [ct for ct in plan_obj.order if ct in scope]
    # Anything in scope but not surfaced by the planner gets
    # appended at the end so we still try to import it.
    seen = set(ordered)
    ordered.extend(sorted(scope - seen))
    return ordered


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def _resolve_body(
    content_type: str,
    body: dict[str, Any],
    openapi: OpenAPI,
    index: NKIndex,
    http: NetboxHTTP,
    registry: Any,
) -> dict[str, Any]:
    """Resolve every FK NK in `body` back to a destination id.

    Three graceful-degrade rules apply per field, so one missing
    reference never aborts the whole import:

    * Simple FK: KeyError on the target lookup -> drop the field
      from the body. The destination row is created without that
      FK; Phase-2 (FEAT-23) can backfill if the target appears
      later in the same run.
    * M2M: each item is resolved independently. Items that 404
      against the destination NK index are dropped from the list;
      surviving items are kept.
    * Polymorphic: same as simple FK, drop the field on KeyError.

    Each soft drop emits a once-per-(content_type, field, target)
    log line so the operator can audit which FKs the import did
    not carry through.
    """

    resolved: dict[str, Any] = {}
    for field_name, value in body.items():
        spec = openapi.field_spec(content_type, field_name)
        if spec.fk_target is None:
            resolved[field_name] = value
            continue
        if spec.is_m2m:
            resolved[field_name] = _safe_resolve_m2m(
                value, spec.fk_target, index, http, registry, content_type, field_name
            )
            continue
        if isinstance(value, dict) and "object_type" in value:
            try:
                resolved[field_name] = resolve_polymorphic(
                    value, index, http=http, registry=registry
                )
            except (KeyError, ValueError) as exc:
                _warn_dropped(content_type, field_name, value.get("object_type", "?"), exc)
            continue
        try:
            resolved[field_name] = resolve_simple_fk(
                value, spec.fk_target, index, http=http, registry=registry
            )
        except (KeyError, ValueError) as exc:
            _warn_dropped(content_type, field_name, spec.fk_target, exc)
            continue
    return resolved


def _safe_resolve_m2m(
    values: Any,
    parent_ct: str,
    index: NKIndex,
    http: NetboxHTTP,
    registry: Any,
    content_type: str,
    field_name: str,
) -> list[int]:
    """Resolve each m2m item independently; drop the ones that miss."""

    from nbsnap.import_.fk_resolve import resolve_simple_fk as resolve_one

    if not isinstance(values, list):
        return []
    out: list[int] = []
    for item in values:
        try:
            resolved = resolve_one(item, parent_ct, index, http=http, registry=registry)
        except (KeyError, ValueError) as exc:
            _warn_dropped(content_type, field_name, parent_ct, exc)
            continue
        if resolved is not None:
            out.append(resolved)
    return out


# Module-level "already warned" sentinel, dedupes per (ct, field, target).
_WARNED_MISSING_FK: set[tuple[str, str, str]] = set()


def _warn_dropped(
    content_type: str, field_name: str, target: str, exc: Exception
) -> None:
    """Log once per (ct, field, target) triple when an FK is dropped."""

    import logging

    key = (content_type, field_name, target)
    if key in _WARNED_MISSING_FK:
        return
    _WARNED_MISSING_FK.add(key)
    logging.getLogger(__name__).warning(
        "dropping FK %s.%s -> %s, %s",
        content_type,
        field_name,
        target,
        exc.args[0] if exc.args else str(exc),
    )
