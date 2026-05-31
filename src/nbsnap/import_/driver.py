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
from collections.abc import Iterator
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


def run_import(
    http: NetboxHTTP,
    snapshot_dir: Path,
    *,
    max_skew: VersionSkew = VersionSkew.MINOR,
    on_error: str = "stop",
    allow_enum_dict_bypass: bool = False,
) -> ImportSummary:
    """Apply the snapshot at `snapshot_dir` to the destination NetBox.

    `allow_enum_dict_bypass` lets a legacy snapshot through even
    when the FEAT-36h scan flags it. The import-side coerce
    still recovers most fields but the round-trip guarantee is
    gone, so use only when re-export is not yet possible.
    """

    snapshot_dir = Path(snapshot_dir)
    manifest = Manifest.load(snapshot_dir / MANIFEST_FILENAME)
    preflight = run_preflight(http, manifest, snapshot_dir=snapshot_dir)
    summary = ImportSummary(preflight=preflight)

    if preflight.is_blocking(
        max_skew, allow_enum_dict_bypass=allow_enum_dict_bypass
    ):
        return summary

    registry = default_registry()
    index = NKIndex()
    openapi = OpenAPI.load(snapshot_dir / SCHEMA_PATH)

    # Look-ahead state for FEAT-36b. Built once and threaded
    # through every _resolve_body call so the demand-driven
    # resolver can pull in missing parents and detect cycles.
    from nbsnap.import_.lookahead import DeferredFK
    from nbsnap.import_.snapshot_index import SnapshotIndex

    snapshot_index = SnapshotIndex.from_snapshot(snapshot_dir)
    deferred_queue: list[DeferredFK] = []
    processing_stack: set[tuple[str, tuple[Any, ...]]] = set()

    auditor = summary.auditor

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
            )
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
        )
        # Phase-2 failures honour the same on_error semantics as
        # Phase-1: under "stop" any failed PATCH aborts; under
        # "continue" they accumulate and the caller sees them via
        # `summary.phase2.failures`.
        if on_error == "stop" and not summary.phase2.is_clean():
            return summary

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
    """Re-export of the shared JSONL streamer in snapshot_index.

    Keeps the existing call sites in `run_import` happy while
    routing the actual logic through the shared helper.
    """
    from nbsnap.import_.snapshot_index import iter_jsonl

    yield from iter_jsonl(path)


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

    # Task #23 pre-pass: resolve paired polymorphic-id fields
    # before the per-field loop. NetBox uses two patterns for
    # generic FKs in WRITE bodies:
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
    )

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
            queue_size_before = (
                len(deferred_queue) if deferred_queue is not None else 0
            )
            recovered = _try_lookahead(
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
            )
            if recovered is not None:
                resolved[field_name] = recovered
                continue
            _record_drop(
                auditor=auditor,
                snapshot_index=snapshot_index,
                deferred_queue=deferred_queue,
                queue_size_before=queue_size_before,
                value=value,
                child_ct=content_type,
                child_nk=current_nk,
                field_name=field_name,
                target_ct=spec.fk_target,
            )
            _warn_dropped(content_type, field_name, spec.fk_target, exc)
            continue
    return resolved


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
    for field_name, value in body.items():
        if not field_name.endswith("_type"):
            continue
        if not isinstance(value, str) or "." not in value:
            continue
        prefix = field_name[: -len("_type")]
        id_field = f"{prefix}_id"
        if id_field not in body:
            continue
        pairs.append((field_name, id_field, value))

    for type_field, id_field, target_ct in pairs:
        raw_id = body[id_field]
        # An already-resolved integer means the write body is
        # ready; skip the pair.
        if isinstance(raw_id, int):
            continue

        # Try resolving against the destination index first.
        try:
            rid = resolve_simple_fk(
                raw_id, target_ct, index, http=http, registry=registry
            )
            new_body[id_field] = rid
            continue
        except (KeyError, ValueError) as exc:
            queue_size_before = (
                len(deferred_queue) if deferred_queue is not None else 0
            )
            # Try the look-ahead path so the parent can be
            # created on demand from the snapshot.
            recovered = _try_lookahead(
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
            )
            if recovered is not None:
                new_body[id_field] = recovered
                continue

            # Total miss, classify the drop for the audit and
            # remove both halves of the pair from the body.
            _record_drop(
                auditor=auditor,
                snapshot_index=snapshot_index,
                deferred_queue=deferred_queue,
                queue_size_before=queue_size_before,
                value=raw_id,
                child_ct=owner_ct,
                child_nk=current_nk,
                field_name=id_field,
                target_ct=target_ct,
            )
            _warn_dropped(owner_ct, id_field, target_ct, exc)
            new_body.pop(id_field, None)
            new_body.pop(type_field, None)

    return new_body


def _record_drop(
    *,
    auditor: Auditor | None,
    snapshot_index: _SnapshotIndexType | None,
    deferred_queue: list[Any] | None,
    queue_size_before: int,
    value: Any,
    child_ct: str,
    child_nk: tuple[Any, ...],
    field_name: str,
    target_ct: str,
) -> None:
    """Classify an FK that the resolver could not place, record it.

    Three categories the operator distinguishes:

    * `DEFERRED_TO_PHASE2` when the look-ahead pushed onto the
      deferred queue, the cycle-breaker is doing its job.
    * `OUT_OF_SCOPE` when the snapshot does not carry the target
      content type at all, the CLAUDE.md "network model only"
      scope excludes it.
    * `MISSING_FROM_SOURCE` when the snapshot covers the target
      content type but is missing this specific NK, real data
      gap on the source.
    """

    if auditor is None:
        return

    target_nk = normalise_nk(value)

    deferred_grew = (
        deferred_queue is not None and len(deferred_queue) > queue_size_before
    )
    if deferred_grew:
        category = DropCategory.DEFERRED_TO_PHASE2
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
) -> int | None:
    """Attempt the FEAT-36b look-ahead path.

    When the look-ahead arguments are missing the helper returns
    None so the caller falls through to the original warn-and-
    drop behaviour. Keeps backwards compatibility with callers
    that have not been threaded yet.

    `openapi` and `auditor` flow into `resolve_or_create` so the
    recursive upsert can route the snapshot body through
    `_resolve_body` before posting (task #22). Without these,
    the look-ahead would POST raw NK-shaped FKs and NetBox would
    reject the create with HTTP 400.
    """

    if (
        snapshot_index is None
        or processing_stack is None
        or deferred_queue is None
    ):
        return None

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
    )
    if rid is not None:
        return rid

    # rid is None: either out of scope, or a cycle. If a cycle,
    # the caller can use the queue-size delta to know we need
    # to push a DeferredFK so Phase-2 picks it up. The
    # resolve_or_create helper does not push the entry itself
    # because only the caller knows the child fields.
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
    return None


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
