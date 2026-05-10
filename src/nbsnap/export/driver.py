"""End-to-end export driver wiring extractor + writer + manifest.

This is what `nbsnap export` calls into. It:

1. Fetches the schema, content-type cache, and status.
2. Builds the dependency graph and runs the planner.
3. Walks each content type in plan order, downloads rows,
   extracts them, writes the JSONL file.
4. Writes the manifest at the end.

The driver stays small on purpose; the heavy lifting lives in the
modules above.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from nbsnap.export.extractor import ExtractedRow, extract
from nbsnap.export.install_local import FlagWriter
from nbsnap.export.manifest import MANIFEST_FILENAME, Manifest, PerfTimer
from nbsnap.export.progress import PROGRESS_FILENAME, ProgressLog, resume_from
from nbsnap.export.writer import write_content_type
from nbsnap.graph import from_openapi
from nbsnap.graph import plan as build_plan
from nbsnap.http.client import NetboxHTTP
from nbsnap.natkey.registry import default as default_registry
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS
from nbsnap.schema.content_types import ContentTypeCache
from nbsnap.schema.openapi import SCHEMA_PATH, OpenAPI
from nbsnap.schema.status import Status

# Renderer-minimum scope, shares the source of truth with plan_cli.
DEFAULT_SCOPE: frozenset[str] = frozenset(CONTENT_TYPE_ENDPOINTS.keys())


def run_export(
    http: NetboxHTTP,
    out_dir: Path,
    *,
    scope: Iterable[str] | None = None,
    resume: bool = False,
) -> Manifest:
    """Export the source NetBox to `out_dir`, return the manifest."""

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = Manifest(
        source_url=http.base_url,
        created_at=dt.datetime.now(dt.UTC).isoformat(),
    )
    perf = PerfTimer(manifest.perf)

    # ------------------------------------------------------------------
    # Phase A, schema discovery
    # ------------------------------------------------------------------
    with perf.timer("schema"):
        openapi = OpenAPI.fetch(http)
        openapi.dump(out_dir / SCHEMA_PATH)

    with perf.timer("content_types"):
        ContentTypeCache.fetch(http)  # cached implicitly, dump later if needed

    with perf.timer("status"):
        status = Status.fetch(http)
        manifest.netbox_version = status.netbox_version

    # ------------------------------------------------------------------
    # Phase B, plan
    # ------------------------------------------------------------------
    effective_scope = set(scope) if scope is not None else set(DEFAULT_SCOPE)
    with perf.timer("plan"):
        graph = from_openapi(openapi, scope=effective_scope)
        plan_obj = build_plan(graph)
        manifest.deferred_edges = [
            {
                "child": e.child,
                "parent": e.parent,
                "field": e.field,
                "nullable": e.nullable,
                "is_m2m": e.is_m2m,
            }
            for e in plan_obj.deferred
        ]

    # ------------------------------------------------------------------
    # Phase C, extract + write
    # ------------------------------------------------------------------
    progress = ProgressLog(out_dir / PROGRESS_FILENAME)
    flag_writer = FlagWriter(out_dir / "flags.jsonl")
    completed: set[str] = resume_from(out_dir / PROGRESS_FILENAME) if resume else set()
    registry = default_registry()
    parent_lookup: dict[tuple[str, int], dict[str, Any]] = {}
    cached_records: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Phase C-1, fetch every content type into parent_lookup BEFORE
    # extracting any of them. The plan order is correct for the import
    # side (parent before child) but the export-side FK rewriter needs
    # to look up parent records for every FK regardless of order,
    # including polymorphic FKs whose targets the planner cannot see
    # statically (Interface, Device referenced by IPAddress's
    # assigned_object_*). A two-pass walk doubles the in-memory
    # footprint briefly but eliminates the "null in composite NK
    # because parent_lookup did not have the parent yet" failure mode.
    # ------------------------------------------------------------------
    for content_type in plan_obj.order:
        if content_type not in effective_scope:
            continue
        if content_type in completed:
            continue
        endpoint = CONTENT_TYPE_ENDPOINTS.get(content_type)
        if endpoint is None:
            continue
        with perf.timer(f"fetch:{content_type}"):
            records = list(http.get_all(endpoint))
            cached_records[content_type] = records
            for record in records:
                rid = record.get("id")
                if isinstance(rid, int):
                    parent_lookup[(content_type, rid)] = record

    # ------------------------------------------------------------------
    # Phase C-2, extract each content type's records using the now-
    # complete parent_lookup. No network IO in this phase, only
    # JSON-shaping and file IO.
    # ------------------------------------------------------------------
    for content_type in plan_obj.order:
        if content_type not in cached_records:
            continue

        with perf.timer(f"extract:{content_type}"):
            rows: list[ExtractedRow] = []
            for extracted, flag in extract(
                content_type,
                iter(cached_records[content_type]),
                openapi=openapi,
                registry=registry,
                parent_lookup=parent_lookup,
                source_url=http.base_url,
            ):
                if extracted is not None:
                    rows.append(extracted)
                if flag is not None:
                    flag_writer.write(flag)
        with perf.timer(f"write:{content_type}"):
            count = write_content_type(out_dir, content_type, rows)
            manifest.counts[content_type] = count
        progress.append(content_type, "all", "done")

    manifest.write(out_dir / MANIFEST_FILENAME)
    return manifest
