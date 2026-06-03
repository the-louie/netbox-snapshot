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

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nbsnap.import_.phase2 import Phase2Summary as _Phase2Summary
    from nbsnap.import_.snapshot_index import SnapshotIndex as _SnapshotIndexType

from nbsnap.export.manifest import MANIFEST_FILENAME, Manifest
from nbsnap.export.writer import CONTENT_TYPE_FILES
from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.audit import Auditor, DropCategory, DropEvent
from nbsnap.import_.fk_resolve import (
    normalise_nk,
    resolve_polymorphic,
    resolve_simple_fk,
)
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.preflight import PreflightReport, run_preflight
from nbsnap.import_.snapshot_index import iter_jsonl
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
    # The deferred-FK queue produced by the FEAT-36b look-ahead
    # resolver during Phase-1, consumed by the FEAT-23 Phase-2
    # writer below. Kept on the summary for tests and for the
    # CLI's audit output.
    deferred_queue: list[Any] = field(default_factory=list)
    # Phase-2 outcomes per cycle-closing PATCH. None when Phase-2
    # did not run (empty queue or preflight blocked).
    phase2: _Phase2Summary | None = None
    # Categorised FK-drop / defer audit (FEAT-36e). Populated
    # during Phase-1 by the resolver via `Auditor.record`. The
    # CLI surfaces this to stderr and to `audit.jsonl`.
    auditor: Auditor = field(default_factory=Auditor)
    # JSONL rows that failed to parse during snapshot load
    # (BUG-06). Each entry is `{path, lineno, message}`. The
    # export pipeline catches malformed bodies upstream; a
    # non-empty list here points at hand-edited or truncated
    # snapshot files.
    parse_errors: list[dict[str, Any]] = field(default_factory=list)
    # FEAT-45b: NKs the look-ahead attempted to create but the
    # destination responded with 5xx. Parallel to failed_keys
    # (which holds permanent 4xx) so the audit can render
    # transient failures as UPSERT_FAILED_TRANSIENT.
    transient_keys: set[tuple[str, tuple[Any, ...]]] = field(default_factory=set)
    # REFACTOR-01a: optional handle to the ResolveContext the
    # driver built for this run. None until run_import attaches
    # one; used by tests that need to introspect resolver state.
    _ctx: Any = None
    # Per-run dedup for `_warn_dropped` (REFACTOR-08). Used to
    # be a module global; making it instance-scoped means two
    # `run_import` calls in the same process both surface
    # their first drop warning instead of the second silently
    # inheriting the first's suppressions.
    _warned_missing_fk: set[tuple[str, str, str]] = field(default_factory=set)
    # FEAT-40: per-content-type SKIPPED count, keyed by
    # content type and reason group. The CLI breaks the summary
    # out so an operator can see which content type lost rows
    # and why, instead of one opaque `skipped: N` line.
    skipped_by_ct: dict[str, dict[str, int]] = field(default_factory=dict)


def run_import(
    http: NetboxHTTP,
    snapshot_dir: Path,
    *,
    max_skew: VersionSkew = VersionSkew.MINOR,
    on_error: str = "stop",
    allow_enum_dict_bypass: bool = False,
    progress: Any = None,
    progress_stream: Any = None,
    progress_audit_path: Path | None = None,
    progress_fsync: bool = False,
    progress_show_timestamps: bool = True,
    phase2_verify: bool = True,
    cache_lookahead_failures: bool = True,
    strict_schema: bool = False,
    use_destination_schema: bool = False,
) -> ImportSummary:
    """Apply the snapshot at `snapshot_dir` to the destination NetBox.

    `allow_enum_dict_bypass` lets a legacy snapshot through even
    when the FEAT-36h scan flags it. The import-side coerce
    still recovers most fields but the round-trip guarantee is
    gone, so use only when re-export is not yet possible.

    Three ways to wire progress reporting (REFACTOR-07):

    * Pass nothing, the driver stays silent.
    * Pass `progress_stream=sys.stderr` (and optionally
      `progress_audit_path`) and the driver builds a
      `ProgressReporter` internally AFTER constructing the
      summary, so the reporter is born with a live auditor
      handle. This is the supported path for the CLI.
    * Pass `progress=<pre-built ProgressReporter>` for tests
      that need to inject a fake reporter. Must have its
      auditor attached at construction (no late binding).
    """

    snapshot_dir = Path(snapshot_dir)
    manifest = Manifest.load(snapshot_dir / MANIFEST_FILENAME)
    # Load the snapshot's OpenAPI early so preflight can use it
    # for the FEAT-46b schema-drift comparison.
    snapshot_openapi = OpenAPI.load(snapshot_dir / SCHEMA_PATH)
    preflight = run_preflight(
        http, manifest,
        snapshot_dir=snapshot_dir,
        snapshot_openapi=snapshot_openapi,
    )
    summary = ImportSummary(preflight=preflight)

    if preflight.is_blocking(
        max_skew,
        allow_enum_dict_bypass=allow_enum_dict_bypass,
        strict_schema=strict_schema,
    ):
        return summary

    registry = default_registry()
    index = NKIndex()
    # FEAT-46c: an opt-in lets the driver use the destination's
    # OpenAPI for body resolution instead of the snapshot's.
    # When the destination has drifted, this avoids resolving
    # against shapes the destination no longer expects.
    if use_destination_schema:
        try:
            openapi = OpenAPI.fetch(http)
        except Exception:  # noqa: BLE001
            openapi = snapshot_openapi
    else:
        openapi = snapshot_openapi

    # Look-ahead state for FEAT-36b. Built once and threaded
    # through every _resolve_body call so the demand-driven
    # resolver can pull in missing parents and detect cycles.
    from nbsnap.import_.lookahead import DeferredFK
    from nbsnap.import_.snapshot_index import SnapshotIndex

    snapshot_index = SnapshotIndex.from_snapshot(
        snapshot_dir, errors=summary.parse_errors
    )
    deferred_queue: list[DeferredFK] = []
    processing_stack: set[tuple[str, tuple[Any, ...]]] = set()
    # Cache of `(content_type, NK)` pairs whose look-ahead
    # create attempt already returned FAILED. Subsequent
    # references to the same parent short-circuit instead of
    # re-issuing the failing POST, converting a per-child
    # retry storm into a single attempt per failed parent.
    # FEAT-45b: callers can disable the cache entirely with
    # `cache_lookahead_failures=False` (--no-lookahead-failure-cache),
    # which means every look-ahead retries. Useful when the
    # destination is going through a transient bad patch.
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None = (
        set() if cache_lookahead_failures else None
    )
    # Index of `content_type -> set[field_name]` for the
    # planner-deferred edges in the manifest. Phase-1 strips
    # these fields from every POST body so NetBox's validators
    # do not refuse the create on a not-yet-bound FK (e.g.
    # Device.primary_ip4 references an IPAddress whose
    # `assigned_object` is not yet set). Phase-2 PATCHes the
    # stripped values back in via the deferred_queue.
    deferred_fields_by_ct: dict[str, set[str]] = {}
    for edge in manifest.deferred_edges:
        child_ct = edge.get("child")
        field_name = edge.get("field")
        if isinstance(child_ct, str) and isinstance(field_name, str):
            deferred_fields_by_ct.setdefault(child_ct, set()).add(field_name)
    # merge in the curated KNOWN_VALIDATION_CYCLES
    # table. These are write-time NetBox validator constraints
    # (e.g. Device.primary_ip4 must point at an IPAddress whose
    # `assigned_object` is one of this device's interfaces)
    # that the static planner cannot see in the schema, so the
    # manifest's deferred_edges does not list them. Merging
    # here makes Phase-1 strip them and queue them for Phase-2
    # the same way it would for a planner-detected cycle.
    from nbsnap.graph.polymorphic import known_validation_cycle_fields
    for ct, fields in known_validation_cycle_fields().items():
        deferred_fields_by_ct.setdefault(ct, set()).update(fields)

    auditor = summary.auditor

    # REFACTOR-01a: bundle the resolver state once so subsequent
    # ticket subtasks (01b, 01c) can migrate call sites to take
    # `ctx` instead of threading ten kwargs through five
    # signatures. Built but not yet consumed; the migration of
    # _try_lookahead/resolve_or_create to read from ctx lands in
    # REFACTOR-01b.
    from nbsnap.import_.resolve_context import ResolveContext
    ctx = ResolveContext(
        http=http,
        index=index,
        registry=registry,
        openapi=openapi,
        snapshot_index=snapshot_index,
        processing_stack=processing_stack,
        deferred_queue=deferred_queue,
        auditor=auditor,
        failed_keys=failed_keys,
        transient_keys=summary.transient_keys,
        deferred_fields_by_ct=deferred_fields_by_ct,
        warn_dedup=summary._warned_missing_fk,
    )
    summary._ctx = ctx  # expose for tests / 01b migration

    # If the caller asked for path-based progress reporting,
    # construct the ProgressReporter here, with the auditor
    # already attached. This is the REFACTOR-07 path that
    # replaces the older "build reporter in the CLI, late-bind
    # the auditor in the driver" flow.
    if progress is None and progress_stream is not None:
        from nbsnap.import_.progress import ProgressReporter
        progress = ProgressReporter(
            stream=progress_stream,
            auditor=auditor,
            audit_path=progress_audit_path,
            fsync=progress_fsync,
            show_timestamps=progress_show_timestamps,
        )

    # Phase-1: per content type, in the order recorded in the
    # manifest. We do not re-plan here; the snapshot is the
    # contract and the manifest is the order.
    for ct in _content_type_order(manifest, snapshot_dir):
        file_path = snapshot_dir / CONTENT_TYPE_FILES.get(
            ct, f"{ct.replace('.', '/')}.jsonl"
        )
        if not file_path.exists():
            continue

        # Count rows up-front so the progress reporter can pick
        # a sensible sample stride. Each JSONL file is at most
        # tens of megabytes for renderer-minimum scope, so the
        # extra streaming pass is cheap.
        rows = list(iter_jsonl(file_path, errors=summary.parse_errors))
        if progress is not None:
            progress.start_phase(ct, total=len(rows))

        for row_index, snapshot_row in enumerate(rows, start=1):
            nk = normalise_nk(snapshot_row.get("natural_key"))
            body = _resolve_body(
                ct,
                snapshot_row.get("body") or {},
                openapi,
                index,
                http,
                registry,
                snapshot_index=snapshot_index,
                processing_stack=processing_stack,
                deferred_queue=deferred_queue,
                current_nk=nk,
                auditor=auditor,
                failed_keys=failed_keys,
                deferred_fields_by_ct=deferred_fields_by_ct,
                warn_dedup=summary._warned_missing_fk,
                transient_keys=summary.transient_keys,
            )
            result = upsert(
                http,
                content_type=ct,
                natural_key=nk,
                body=body,
                index=index,
                registry=registry,
                auditor=auditor,
            )
            summary.counts[result.outcome] += 1
            if result.outcome is UpsertOutcome.SKIPPED:
                # FEAT-40: break SKIPPED out by content type and
                # reason group so the CLI summary can show
                # actionable per-ct totals instead of one opaque
                # number. Reason groups come from the message
                # prefix; we pull the first part before "(" or
                # the colon as a stable key.
                bucket = summary.skipped_by_ct.setdefault(ct, {})
                reason = _skip_reason_group(result.message or "")
                bucket[reason] = bucket.get(reason, 0) + 1
            if progress is not None:
                progress.tick(ct, row_index)
            if result.outcome is UpsertOutcome.FAILED:
                summary.failures.append(result)
                if on_error == "stop":
                    if progress is not None:
                        progress.close()
                    return summary

        if progress is not None:
            progress.end_phase(ct)

        # BUG-03: once the extras.customfield phase has run, the
        # destination customfield registry is authoritative and
        # the CF filter can safely strip unknown keys. Bust the
        # cache so the next lookup re-reads the definitions
        # this phase just landed.
        if ct == "extras.customfield" and hasattr(http, "mark_cf_phase_complete"):
            http.mark_cf_phase_complete()

    # Surface the deferred queue from Phase-1 on the summary
    # so the CLI audit and integration tests can see it.
    summary.deferred_queue = deferred_queue

    # Phase-2 (FEAT-23): walk the deferred queue produced by the
    # look-ahead resolver and PATCH each cycle-closing FK. Each
    # entry carries the (child, parent, field) triple needed for a
    # one-field PATCH against the destination. If Phase-1 returned
    # a clean run with no deferrals, this is a no-op.
    if deferred_queue:
        from nbsnap.import_.phase2 import run_phase2

        summary.phase2 = run_phase2(
            http,
            deferred_queue,
            dest_index=index,
            registry=registry,
            verify=phase2_verify,
        )
        # Phase-2 failures honour the same on_error semantics as
        # Phase-1: under "stop" any failed PATCH aborts; under
        # "continue" they accumulate and the caller sees them via
        # `summary.phase2.failures`.
        if on_error == "stop" and not summary.phase2.is_clean():
            if progress is not None:
                progress.close()
            return summary

    if progress is not None:
        progress.close()
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


def _resolve_body(
    content_type: str,
    body: dict[str, Any],
    openapi: OpenAPI,
    index: NKIndex,
    http: NetboxHTTP,
    registry: Any,
    *,
    snapshot_index: _SnapshotIndexType | None = None,
    processing_stack: set[tuple[str, tuple[Any, ...]]] | None = None,
    deferred_queue: list[Any] | None = None,
    current_nk: tuple[Any, ...] = (),
    auditor: Auditor | None = None,
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
    deferred_fields_by_ct: dict[str, set[str]] | None = None,
    warn_dedup: set[tuple[str, str, str]] | None = None,
    transient_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
) -> dict[str, Any]:
    """Resolve every FK NK in `body` back to a destination id.

    Three graceful-degrade rules apply per field, so one missing
    reference never aborts the whole import:

    * Simple FK: try `resolve_simple_fk` against the destination
      first; on a miss, fall back to the look-ahead resolver
      (FEAT-36b) which consults the SnapshotIndex and creates
      the target on demand. If both miss, drop the field with
      a warning.
    * M2M: each item is resolved independently. Items that 404
      against the destination NK index are dropped from the list;
      surviving items are kept.
    * Polymorphic: same as simple FK, drop the field on KeyError.

    Each soft drop emits a once-per-(content_type, field, target)
    log line so the operator can audit which FKs the import did
    not carry through.

    The four look-ahead keyword arguments (`snapshot_index`,
    `processing_stack`, `deferred_queue`, `current_nk`) are
    optional so existing callers (and the existing tests) that
    do not pass them keep working with the destination-only
    behaviour. When all four are present, the look-ahead path
    is wired into the simple-FK branch.
    """

    # Pre-pass: resolve paired polymorphic-id fields before
    # the per-field loop. NetBox uses two patterns for generic
    # FKs in WRITE bodies:
    #
    #   (a) the unified shape `{"object_type": "dcim.interface",
    #       "object_id": <int>}` (handled in the loop below).
    #   (b) the paired shape, two sibling fields side-by-side:
    #       `assigned_object_type: "dcim.interface"` and
    #       `assigned_object_id: <int>`. The snapshot stores the
    #       _id field with the target's natural key, not the
    #       int, so the resolver has to translate it before the
    #       POST or NetBox returns HTTP 400.
    #
    # The pre-pass walks the body once, finds pairs, resolves
    # the `_id` value against the destination index for the
    # content type named in the `_type` field, and replaces the
    # _id with the destination integer id. The standard loop
    # below then sees an already-resolved int and passes it
    # through untouched.
    # Pre-resolution strip (REFACTOR-05). Pull the deferred-edge
    # fields out before any FK resolver runs. If we let the
    # field loop touch them first, a resolver miss would drop
    # the field silently and Phase-2 would never PATCH; doing
    # the strip first guarantees the DeferredFK queues even when
    # the target is currently unresolvable on the destination.
    # Phase-2 looks up the target NK against the destination
    # NKIndex at PATCH time, so the queue only needs the raw NK
    # form the snapshot already carries.
    body = _strip_deferred_fields_and_queue(
        body,
        content_type=content_type,
        current_nk=current_nk,
        original_body=body,
        deferred_fields_by_ct=deferred_fields_by_ct,
        deferred_queue=deferred_queue,
        openapi=openapi,
        auditor=auditor,
    )

    body = _resolve_polymorphic_id_pairs(
        body,
        openapi,
        index,
        http,
        registry,
        snapshot_index=snapshot_index,
        processing_stack=processing_stack,
        deferred_queue=deferred_queue,
        current_nk=current_nk,
        auditor=auditor,
        owner_ct=content_type,
        failed_keys=failed_keys,
        deferred_fields_by_ct=deferred_fields_by_ct,
        warn_dedup=warn_dedup,
    )

    # Pre-pass: resolve Cable termination dicts. The
    # snapshot stores cable terminations as
    # `[{"object_natural_key": [...nk...], "object_type":
    # "dcim.interface"}, ...]`, NetBox expects `object_id: <int>`
    # in each dict. Drive the conversion here so the standard
    # field loop only sees write-ready shapes.
    body = _resolve_termination_lists(
        body,
        openapi,
        index,
        http,
        registry,
        snapshot_index=snapshot_index,
        processing_stack=processing_stack,
        deferred_queue=deferred_queue,
        current_nk=current_nk,
        auditor=auditor,
        owner_ct=content_type,
        failed_keys=failed_keys,
        deferred_fields_by_ct=deferred_fields_by_ct,
        warn_dedup=warn_dedup,
    )

    resolved: dict[str, Any] = {}
    for field_name, value in body.items():
        spec = openapi.field_spec(content_type, field_name)
        if spec.fk_target is None:
            resolved[field_name] = value
            continue
        if spec.is_m2m:
            resolved[field_name] = _safe_resolve_m2m(
                value, spec.fk_target, index, http, registry, content_type, field_name,
                snapshot_index=snapshot_index,
                auditor=auditor,
                current_nk=current_nk,
                failed_keys=failed_keys,
                warn_dedup=warn_dedup,
            )
            continue
        if isinstance(value, dict) and "object_type" in value:
            try:
                resolved[field_name] = resolve_polymorphic(
                    value, index, http=http, registry=registry
                )
            except (KeyError, ValueError) as exc:
                _warn_dropped(
                    content_type, field_name,
                    value.get("object_type", "?"), exc,
                    warn_dedup=warn_dedup,
                )
            continue
        try:
            resolved[field_name] = resolve_simple_fk(
                value, spec.fk_target, index, http=http, registry=registry
            )
        except (KeyError, ValueError) as exc:
            queue_size_before = (
                len(deferred_queue) if deferred_queue is not None else 0
            )
            recovered, was_deferred = _try_lookahead(
                value=value,
                target_ct=spec.fk_target,
                http=http,
                index=index,
                registry=registry,
                snapshot_index=snapshot_index,
                processing_stack=processing_stack,
                deferred_queue=deferred_queue,
                child_ct=content_type,
                child_nk=current_nk,
                field_name=field_name,
                openapi=openapi,
                auditor=auditor,
                failed_keys=failed_keys,
                deferred_fields_by_ct=deferred_fields_by_ct,
                transient_keys=transient_keys,
            )
            if recovered is not None:
                resolved[field_name] = recovered
                continue
            category = _record_drop(
                auditor=auditor,
                snapshot_index=snapshot_index,
                deferred_queue=deferred_queue,
                value=value,
                child_ct=content_type,
                child_nk=current_nk,
                field_name=field_name,
                target_ct=spec.fk_target,
                failed_keys=failed_keys,
                transient_keys=transient_keys,
                was_deferred=was_deferred,
            )
            # Suppress the per-row warning when the audit
            # classified this as OUT_OF_SCOPE (documented
            # network-only scope behaviour) or
            # DEFERRED_TO_PHASE2 (Phase-2 will PATCH the field).
            # Both categories add operator noise without
            # actionable signal; the audit summary at
            # end-of-run carries the counts.
            #
            # `category is None` means no auditor was wired in
            # (the backwards-compat path used by some unit
            # tests). In that case we cannot tell which
            # bucket the drop belongs to, so we keep the
            # legacy warn-everything behaviour. The audit is
            # the source of truth for the suppression rule, so
            # turning the auditor off naturally falls back to
            # the noisier-but-safer path.
            if category not in (DropCategory.OUT_OF_SCOPE, DropCategory.DEFERRED_TO_PHASE2):
                _warn_dropped(
                    content_type, field_name, spec.fk_target, exc,
                    category=category,
                    warn_dedup=warn_dedup,
                )
            continue

    return resolved


def _strip_deferred_fields_and_queue(
    resolved: dict[str, Any],
    *,
    content_type: str,
    current_nk: tuple[Any, ...],
    original_body: dict[str, Any],
    deferred_fields_by_ct: dict[str, set[str]] | None,
    auditor: Auditor | None = None,
    deferred_queue: list[Any] | None,
    openapi: OpenAPI,
) -> dict[str, Any]:
    """Strip the planner-deferred FK fields from `resolved` and
    push corresponding DeferredFK entries onto the queue.

    The manifest's deferred_edges identifies which (child_ct,
    field) pairs the planner marked for Phase-2. Phase-1 must
    POST those records WITHOUT the FK set, then Phase-2 PATCHes
    the FK in after both endpoints exist on the destination.

    For each field that:

    * is listed as deferred for this content type, AND
    * has a non-None value in `resolved` (the simple-FK branch
      already resolved it, or look-ahead created the target),

    we:

    1. Remove the field from `resolved` so the upsert POST
       does not include it.
    2. Push a `DeferredFK` onto `deferred_queue` carrying the
       child record's NK + the field name + the target's
       content type + the target's natural key (lifted from
       the original snapshot body). Phase-2 looks the target
       up against the destination NKIndex at PATCH time.

    Returns a new dict if anything was stripped, otherwise the
    input `resolved` unchanged.
    """

    if not deferred_fields_by_ct or deferred_queue is None:
        return resolved
    fields = deferred_fields_by_ct.get(content_type)
    if not fields:
        return resolved

    # Lazy imports keep the module graph acyclic: this helper
    # is part of driver.py, the DeferredFK type lives in
    # lookahead.py which imports from driver during a runtime
    # callout, so we touch DeferredFK only on the deferred-
    # found path.
    from nbsnap.import_.fk_resolve import normalise_nk
    from nbsnap.import_.lookahead import DeferredFK

    # Dedupe: a record can flow through `_resolve_body` twice,
    # once via the look-ahead's recursive callout and once via
    # the main Phase-1 phase. Without this guard we would push
    # two identical DeferredFKs onto the queue, doubling
    # Phase-2's work and (worse) PATCHing twice if the second
    # PATCH had a different value.
    existing_keys = {
        (entry.child_content_type, entry.child_nk, entry.field_name)
        for entry in deferred_queue
    }

    out = resolved
    for field_name in fields:
        if field_name not in out:
            continue
        if out[field_name] is None:
            # Nothing to defer, the field is empty already.
            # Drop it so the body stays consistent with the
            # "we PATCH this later" promise.
            if out is resolved:
                out = dict(resolved)
            del out[field_name]
            continue

        spec = openapi.field_spec(content_type, field_name)
        target_ct = spec.fk_target
        # We need a target content type and an original NK to
        # tell Phase-2 what to look up. If either is missing we
        # cannot defer cleanly, leave the resolved value alone
        # and let NetBox reject if it must. Better an
        # actionable error than silent data loss.
        if target_ct is None:
            continue
        raw_value = original_body.get(field_name)
        if raw_value is None:
            continue
        target_nk = normalise_nk(raw_value)

        # First time we mutate, copy `resolved` so the caller's
        # input dict is never touched.
        if out is resolved:
            out = dict(resolved)
        del out[field_name]

        dedupe_key = (content_type, current_nk, field_name)
        if dedupe_key in existing_keys:
            # Already queued by an earlier pass (e.g. the
            # look-ahead created this record before the main
            # phase did). Stripping the field again is fine
            # (idempotent on a dict), but pushing the
            # DeferredFK again would double Phase-2's work.
            continue
        existing_keys.add(dedupe_key)
        deferred_queue.append(
            DeferredFK(
                child_content_type=content_type,
                child_nk=current_nk,
                field_name=field_name,
                target_content_type=target_ct,
                target_nk=target_nk,
            )
        )
        if auditor is not None:
            auditor.record(DropEvent(
                category=DropCategory.DEFERRED_TO_PHASE2,
                child_content_type=content_type,
                child_nk=current_nk,
                field_name=field_name,
                target_content_type=target_ct,
                target_nk=target_nk,
            ))
    return out


def _resolve_polymorphic_id_pairs(
    body: dict[str, Any],
    openapi: OpenAPI,  # forwarded to _try_lookahead for body-resolution recursion
    index: NKIndex,
    http: NetboxHTTP,
    registry: Any,
    *,
    snapshot_index: _SnapshotIndexType | None,
    processing_stack: set[tuple[str, tuple[Any, ...]]] | None,
    deferred_queue: list[Any] | None,
    current_nk: tuple[Any, ...],
    auditor: Auditor | None,
    owner_ct: str,
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
    deferred_fields_by_ct: dict[str, set[str]] | None = None,
    warn_dedup: set[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Resolve `<prefix>_type` + `<prefix>_id` paired polymorphic FKs.

    NetBox writes generic FKs in two shapes, see the comment in
    `_resolve_body` for the full motivation. This helper handles
    the paired shape only; the unified dict shape is handled in
    the main loop.

    Detection rule, the body has a pair when both:

    * a field whose name ends in `_type` carries a string that
      looks like a NetBox content type (`app.model`); AND
    * a sibling field with the same prefix and the `_id` suffix
      exists.

    Resolution rule, for each such pair:

    1. If the `_id` field is already an integer, leave both
       fields alone, the body was already in write-ready shape.
    2. Otherwise treat the `_id` value as the target's natural
       key, resolve it via `resolve_simple_fk` against the
       destination index for the content type named in `_type`.
    3. On a destination-index miss, fall back to the
       FEAT-36b look-ahead path so the parent gets created on
       demand and the resolved id is then patched into the body.
    4. On total miss, drop both fields, the audit log records
       the FK with the same MISSING / OUT_OF_SCOPE category as
       a normal FK drop so the operator sees the same picture.

    The pair stays together throughout, the destination treats
    a write that drops only one half as a validation error, so
    we drop both or neither.

    Returns a new dict, the input `body` is not mutated.
    """

    new_body = dict(body)

    # Find every `_type` field whose value is a content-type
    # string AND whose sibling `_id` field exists. We list the
    # pairs first then resolve in a second pass so adding the
    # resolved id does not perturb the iteration.
    pairs: list[tuple[str, str, str]] = []
    # NetBox treats `<prefix>_type` and `<prefix>_id` as a unit
    # on writes: setting one without the other surfaces as
    # "field cannot be null" on the missing half. We treat the
    # `_type` field as part of a pair whenever its value is a
    # content-type-shaped string, even if the sibling `_id`
    # is absent from the body. That lets the loop below drop
    # both fields cleanly (rather than letting a stray `_type`
    # leak into the POST).
    for field_name, value in body.items():
        if not field_name.endswith("_type"):
            continue
        if not isinstance(value, str) or "." not in value:
            continue
        prefix = field_name[: -len("_type")]
        id_field = f"{prefix}_id"
        pairs.append((field_name, id_field, value))

    for type_field, id_field, target_ct in pairs:
        raw_id = body.get(id_field)
        # An already-resolved integer means the write body is
        # ready; skip the pair.
        if isinstance(raw_id, int):
            continue

        # A null OR absent `_id` (paired with a non-null
        # `_type`) means the source row expresses an
        # intentionally-unbound polymorphic FK. NetBox refuses
        # `..._id: null` and refuses a lone `_type` for the
        # same reason; the legal write shape is to omit both
        # halves so the record creates with the FK unbound.
        if raw_id is None:
            new_body.pop(id_field, None)
            new_body.pop(type_field, None)
            continue

        # Try resolving against the destination index first.
        try:
            rid = resolve_simple_fk(
                raw_id, target_ct, index, http=http, registry=registry
            )
            if rid is None:
                # Resolver returned None without raising
                # (e.g. value is not list-shaped). Same fix as
                # the explicit-null branch above: drop both
                # halves so NetBox sees a legal write shape.
                new_body.pop(id_field, None)
                new_body.pop(type_field, None)
                continue
            new_body[id_field] = rid
            continue
        except (KeyError, ValueError) as exc:
            queue_size_before = (
                len(deferred_queue) if deferred_queue is not None else 0
            )
            # Try the look-ahead path so the parent can be
            # created on demand from the snapshot.
            recovered, was_deferred = _try_lookahead(
                value=raw_id,
                target_ct=target_ct,
                http=http,
                index=index,
                registry=registry,
                snapshot_index=snapshot_index,
                processing_stack=processing_stack,
                deferred_queue=deferred_queue,
                child_ct=owner_ct,
                child_nk=current_nk,
                field_name=id_field,
                openapi=openapi,
                auditor=auditor,
                failed_keys=failed_keys,
                deferred_fields_by_ct=deferred_fields_by_ct,
            )
            if recovered is not None:
                new_body[id_field] = recovered
                continue

            # Total miss, classify the drop for the audit and
            # remove both halves of the pair from the body.
            category = _record_drop(
                auditor=auditor,
                snapshot_index=snapshot_index,
                deferred_queue=deferred_queue,
                was_deferred=was_deferred,
                value=raw_id,
                child_ct=owner_ct,
                child_nk=current_nk,
                field_name=id_field,
                target_ct=target_ct,
                failed_keys=failed_keys,
            )
            if category not in (DropCategory.OUT_OF_SCOPE, DropCategory.DEFERRED_TO_PHASE2):
                _warn_dropped(
                    owner_ct, id_field, target_ct, exc, category=category,
                    warn_dedup=warn_dedup,
                )
            new_body.pop(id_field, None)
            new_body.pop(type_field, None)

    return new_body


def _resolve_termination_lists(
    body: dict[str, Any],
    openapi: OpenAPI,
    index: NKIndex,
    http: NetboxHTTP,
    registry: Any,
    *,
    snapshot_index: _SnapshotIndexType | None,
    processing_stack: set[tuple[str, tuple[Any, ...]]] | None,
    deferred_queue: list[Any] | None,
    current_nk: tuple[Any, ...],
    auditor: Auditor | None,
    owner_ct: str,
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
    deferred_fields_by_ct: dict[str, set[str]] | None = None,
    warn_dedup: set[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Convert termination dicts from snapshot to NetBox shape.

    The snapshot stores cable terminations like this:

        "a_terminations": [
            {"object_natural_key": [...nk_tuple...],
             "object_type": "dcim.interface"}
        ]

    NetBox's write API expects:

        "a_terminations": [
            {"object_id": <int>,
             "object_type": "dcim.interface"}
        ]

    The conversion needs both halves of each item, so we cannot
    delegate to the per-field loop below. This pre-pass walks
    every list-of-dict field, detects the
    `object_natural_key + object_type` pattern, resolves the
    natural key against the destination NKIndex for the named
    content type, and rewrites the dict with `object_id`.

    On a miss the whole item is dropped from the list; NetBox
    rejects writes that mix resolved and unresolved
    terminations. If every item in a list drops, the field
    itself is dropped so NetBox sees a clean empty pair rather
    than an explicit `[]` that would surface as "required field
    is empty" on cables.

    Returns a new dict, the input `body` is not mutated.
    """

    new_body = dict(body)
    for field_name, value in body.items():
        if not isinstance(value, list):
            continue
        # Spot-check: is this a list of termination dicts? We
        # treat the field as a termination list if at least one
        # item has `object_natural_key` + `object_type`. Other
        # list-of-dict shapes (e.g. tags carrying brief refs)
        # are left alone.
        if not any(
            isinstance(item, dict)
            and "object_natural_key" in item
            and "object_type" in item
            for item in value
        ):
            continue

        resolved_items: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            target_ct = item.get("object_type")
            raw_nk = item.get("object_natural_key")
            if not isinstance(target_ct, str) or raw_nk is None:
                # Malformed item, skip rather than crash. The
                # operator sees the missing termination via the
                # destination's cable failure, not via a Python
                # traceback.
                continue

            # Resolve against the destination index first.
            try:
                rid = resolve_simple_fk(
                    raw_nk, target_ct, index, http=http, registry=registry
                )
                resolved_items.append({
                    "object_type": target_ct, "object_id": rid,
                })
                continue
            except (KeyError, ValueError) as exc:
                queue_size_before = (
                    len(deferred_queue) if deferred_queue is not None else 0
                )
                # Look-ahead path so the target interface can be
                # created from the snapshot if it is in scope.
                recovered, was_deferred = _try_lookahead(
                    value=raw_nk,
                    target_ct=target_ct,
                    http=http,
                    index=index,
                    registry=registry,
                    snapshot_index=snapshot_index,
                    processing_stack=processing_stack,
                    deferred_queue=deferred_queue,
                    child_ct=owner_ct,
                    child_nk=current_nk,
                    field_name=field_name,
                    openapi=openapi,
                    auditor=auditor,
                    failed_keys=failed_keys,
                    deferred_fields_by_ct=deferred_fields_by_ct,
                )
                if recovered is not None:
                    resolved_items.append({
                        "object_type": target_ct, "object_id": recovered,
                    })
                    continue
                # Total miss, record the drop and skip the item.
                category = _record_drop(
                    auditor=auditor,
                    snapshot_index=snapshot_index,
                    deferred_queue=deferred_queue,
                    was_deferred=was_deferred,
                    value=raw_nk,
                    child_ct=owner_ct,
                    child_nk=current_nk,
                    field_name=field_name,
                    target_ct=target_ct,
                    failed_keys=failed_keys,
                )
                if category not in (DropCategory.OUT_OF_SCOPE, DropCategory.DEFERRED_TO_PHASE2):
                    _warn_dropped(
                        owner_ct, field_name, target_ct, exc,
                        category=category,
                        warn_dedup=warn_dedup,
                    )

        if resolved_items:
            new_body[field_name] = resolved_items
        else:
            # All items dropped, remove the field entirely. A
            # cable POST with `a_terminations: []` is rejected
            # by NetBox as "required field is empty"; dropping
            # the key surfaces the cleaner "required" error
            # which the operator can act on.
            new_body.pop(field_name, None)

    return new_body


def _record_drop(
    *,
    auditor: Auditor | None,
    snapshot_index: _SnapshotIndexType | None,
    deferred_queue: list[Any] | None,
    queue_size_before: int = 0,
    value: Any,
    child_ct: str,
    child_nk: tuple[Any, ...],
    field_name: str,
    target_ct: str,
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
    transient_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
    was_deferred: bool | None = None,
) -> DropCategory | None:
    """Classify an FK that the resolver could not place, record it.

    Four categories the operator distinguishes:

    * `DEFERRED_TO_PHASE2` when the look-ahead pushed onto the
      deferred queue, the cycle-breaker is doing its job.
    * `UPSERT_FAILED` when the look-ahead actually tried to
      create the referenced parent and NetBox refused (the
      target's NK is in `failed_keys`). The audit summary
      surfaces these as destination/policy issues rather than
      "missing from source", which would mis-bias the
      operator's data-quality assessment.
    * `OUT_OF_SCOPE` when the snapshot does not carry the
      target content type at all, the CLAUDE.md "network
      model only" scope excludes it by design.
    * `MISSING_FROM_SOURCE` when the snapshot covers the
      target content type but is missing this specific NK,
      a real data gap on the source.

    Returns the chosen category so the caller can adjust its
    warning behaviour, the suppression of stderr "dropping
    FK" lines for OUT_OF_SCOPE drops is the operator-noise
    motivation. Returns `None` when no auditor is wired in
    (the call is a no-op and the caller falls back to the
    legacy warn-everything path).
    """

    if auditor is None:
        return None

    target_nk = normalise_nk(value)

    # BUG-04: prefer the explicit `was_deferred` signal from
    # `_try_lookahead` over the queue-size-delta proxy, which
    # mis-attributed sibling-field deferrals to the current
    # field. The proxy is kept as a fallback for callers that
    # have not been threaded yet.
    if was_deferred is None:
        deferred_grew = (
            deferred_queue is not None and len(deferred_queue) > queue_size_before
        )
    else:
        deferred_grew = was_deferred
    if deferred_grew:
        category = DropCategory.DEFERRED_TO_PHASE2
    elif (
        transient_keys is not None
        and (target_ct, target_nk) in transient_keys
    ):
        # FEAT-45b: 5xx from the destination at the look-ahead
        # site. Distinct bucket so the operator sees this is
        # environment, not data quality.
        category = DropCategory.UPSERT_FAILED_TRANSIENT
    elif (
        failed_keys is not None
        and (target_ct, target_nk) in failed_keys
    ):
        # A previous look-ahead create attempt for this exact
        # target NK already FAILED. The operator-meaningful
        # signal is "upsert refused", not "missing from source".
        category = DropCategory.UPSERT_FAILED
    elif snapshot_index is not None and not snapshot_index.has_content_type(target_ct):
        category = DropCategory.OUT_OF_SCOPE
    else:
        category = DropCategory.MISSING_FROM_SOURCE

    auditor.record(DropEvent(
        category=category,
        child_content_type=child_ct,
        child_nk=child_nk,
        field_name=field_name,
        target_content_type=target_ct,
        target_nk=target_nk,
    ))
    return category


def _try_lookahead(
    *,
    value: Any,
    target_ct: str,
    http: NetboxHTTP,
    index: NKIndex,
    registry: Any,
    snapshot_index: _SnapshotIndexType | None,
    processing_stack: set[tuple[str, tuple[Any, ...]]] | None,
    deferred_queue: list[Any] | None,
    child_ct: str,
    child_nk: tuple[Any, ...],
    field_name: str,
    openapi: OpenAPI | None = None,
    auditor: Auditor | None = None,
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
    deferred_fields_by_ct: dict[str, set[str]] | None = None,
    transient_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
    ctx: Any = None,
) -> tuple[int | None, bool]:
    """Attempt the FEAT-36b look-ahead path.

    Returns `(rid, was_deferred)`. `was_deferred` is True iff
    this call (rather than a recursive sibling) pushed a
    `DeferredFK` for the current field; BUG-04 replaced the
    earlier queue-size-delta proxy with an explicit signal so
    sibling deferrals can no longer cause cross-field
    mis-classification.

    When the look-ahead arguments are missing the helper returns
    `(None, False)` so the caller falls through to the original
    warn-and-drop behaviour. Keeps backwards compatibility with
    callers that have not been threaded yet.

    `openapi` and `auditor` flow into `resolve_or_create` so the
    recursive upsert can route the snapshot body through
    `_resolve_body` before posting. Without these,
    the look-ahead would POST raw NK-shaped FKs and NetBox would
    reject the create with HTTP 400.
    """

    # REFACTOR-01b: ctx-based unwrap. The legacy kwarg shape
    # stays supported so existing callers (and tests) work
    # unchanged.
    if ctx is not None:
        snapshot_index = snapshot_index if snapshot_index is not None else ctx.snapshot_index
        processing_stack = processing_stack if processing_stack is not None else ctx.processing_stack
        deferred_queue = deferred_queue if deferred_queue is not None else ctx.deferred_queue
        openapi = openapi if openapi is not None else ctx.openapi
        auditor = auditor if auditor is not None else ctx.auditor
        if failed_keys is None:
            failed_keys = ctx.failed_keys
        if transient_keys is None:
            transient_keys = ctx.transient_keys
        if deferred_fields_by_ct is None:
            deferred_fields_by_ct = ctx.deferred_fields_by_ct

    if (
        snapshot_index is None
        or processing_stack is None
        or deferred_queue is None
    ):
        return None, False

    # The snapshot stores NKs as lists; the resolver wants
    # tuples. Convert here so the look-ahead module does not
    # need to know about list-vs-tuple normalisation.
    from nbsnap.import_.fk_resolve import normalise_nk
    from nbsnap.import_.lookahead import DeferredFK, resolve_or_create

    target_nk = normalise_nk(value)

    queue_size_before = len(deferred_queue)
    rid = resolve_or_create(
        http,
        snapshot_index,
        index,
        registry,
        content_type=target_ct,
        natural_key=target_nk,
        processing_stack=processing_stack,
        deferred_queue=deferred_queue,
        openapi=openapi,
        auditor=auditor,
        failed_keys=failed_keys,
        deferred_fields_by_ct=deferred_fields_by_ct,
        transient_keys=transient_keys,
    )
    if rid is not None:
        return rid, False

    # rid is None: either out of scope, or a cycle. If a cycle,
    # push a DeferredFK so Phase-2 picks it up; tag the return
    # so the caller knows this specific field deferred (not a
    # sibling earlier in the same record).
    was_deferred = False
    if len(deferred_queue) == queue_size_before and (target_ct, target_nk) in processing_stack:
        deferred_queue.append(
            DeferredFK(
                child_content_type=child_ct,
                child_nk=child_nk,
                field_name=field_name,
                target_content_type=target_ct,
                target_nk=target_nk,
            )
        )
        was_deferred = True
    return None, was_deferred


def _safe_resolve_m2m(
    values: Any,
    parent_ct: str,
    index: NKIndex,
    http: NetboxHTTP,
    registry: Any,
    content_type: str,
    field_name: str,
    *,
    snapshot_index: _SnapshotIndexType | None = None,
    auditor: Auditor | None = None,
    current_nk: tuple[Any, ...] = (),
    failed_keys: set[tuple[str, tuple[Any, ...]]] | None = None,
    warn_dedup: set[tuple[str, str, str]] | None = None,
) -> list[int]:
    """Resolve each m2m item independently; drop the ones that miss.

    Per-item drops are recorded on the audit log with the same
    classifier as simple FKs so the operator sees a consistent
    picture, M2M misses on `tags` and `tagged_vlans` should
    surface as OUT_OF_SCOPE or MISSING_FROM_SOURCE rows in the
    summary instead of vanishing entirely.
    """

    from nbsnap.import_.fk_resolve import resolve_simple_fk as resolve_one

    if not isinstance(values, list):
        return []
    out: list[int] = []
    for item in values:
        try:
            resolved = resolve_one(item, parent_ct, index, http=http, registry=registry)
        except (KeyError, ValueError) as exc:
            _record_drop(
                auditor=auditor,
                snapshot_index=snapshot_index,
                deferred_queue=None,
                queue_size_before=0,
                value=item,
                child_ct=content_type,
                child_nk=current_nk,
                field_name=field_name,
                target_ct=parent_ct,
                failed_keys=failed_keys,
            )
            _warn_dropped(
                content_type, field_name, parent_ct, exc,
                warn_dedup=warn_dedup,
            )
            continue
        if resolved is not None:
            out.append(resolved)
    return out


def _skip_reason_group(message: str) -> str:
    """Compress an upsert SKIPPED message to a short reason key.

    NetBox/nbsnap SKIPPED messages are free-text today; we
    take the substring up to the first colon or parenthesis
    and trim it. Known reason groups:

    * `no resolvable terminations` for dcim.cable.
    * `duplicate IP in global table` for ipam.ipaddress.
    * `overlap with existing range` for ipam.iprange.
    * unknown messages collapse to `other`.

    FEAT-40 surfaces this key in the CLI summary so the
    operator does not have to grep audit.jsonl.
    """

    if not message:
        return "other"
    head = message.split(":", 1)[0].split("(", 1)[0].strip()
    return head or "other"


def _warn_dropped(
    content_type: str,
    field_name: str,
    target: str,
    exc: Exception,
    *,
    category: DropCategory | None = None,
    warn_dedup: set[tuple[str, str, str]] | None = None,
) -> None:
    """Log once per (ct, field, target) triple when an FK is dropped.

    The message is category-aware so the operator's first
    investigation target matches the actual fault site, see
    `BUG-08`:

    * `MISSING_FROM_SOURCE`, the source NetBox referenced a
      target that is neither in the snapshot nor on the
      destination. The fix is upstream of this tool.
    * `UPSERT_FAILED`, the destination NetBox refused the
      create. The audit log carries the failure body.
    * Anything else (including `None`, which means the
      auditor was not wired in for this call site) keeps the
      legacy generic "dropping FK" phrasing.
    """

    import logging

    if warn_dedup is not None:
        key = (content_type, field_name, target)
        if key in warn_dedup:
            return
        warn_dedup.add(key)
    detail = exc.args[0] if exc.args else str(exc)
    log = logging.getLogger(__name__)
    if category is DropCategory.MISSING_FROM_SOURCE:
        log.warning(
            "source NetBox has a stale or broken reference: "
            "%s.%s -> %s, the target is not in the snapshot or "
            "on the destination (%s). Rebuild the snapshot from "
            "a freshly-exported source.",
            content_type, field_name, target, detail,
        )
    elif category is DropCategory.UPSERT_FAILED:
        log.warning(
            "destination NetBox refused the create for "
            "%s.%s -> %s (%s). See audit log for the failure body.",
            content_type, field_name, target, detail,
        )
    else:
        log.warning(
            "dropping FK %s.%s -> %s, %s",
            content_type, field_name, target, detail,
        )
