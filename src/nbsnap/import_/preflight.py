"""Pre-flight checks (FEAT-18a/b/c plus FEAT-36h enum-dict scan).

Four checks run before any write to the destination:

* **Version and format compatibility.** Manifest's snapshot
  format version vs the importer's. Destination NetBox version
  vs the source's, gated by `--max-version-skew`.
* **Content-type coverage.** Every content type in the manifest
  must exist on the destination. Missing types abort with a
  clear list.
* **Custom-field reconciliation.** Custom fields the snapshot
  references but the destination lacks are reported.
* **Snapshot format scan (FEAT-36h).** Detects the legacy
  `{value, label}` enum-dict shape that pre-blocker snapshots
  carry; refuses the import unless the operator opts in via
  `--allow-enum-dict-bypass`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nbsnap.snapshot import Manifest
from nbsnap.snapshot.layout import UnknownContentTypeError, relative_path
from nbsnap.http.client import NetboxHTTP
from nbsnap.schema.content_types import ContentTypeCache
from nbsnap.schema.status import Status, VersionSkew

# Sentinel set of keys identifying a NetBox enum dict. NetBox
# GETs return `{"value": "...", "label": "..."}` for choice
# fields; POST/PATCH require the bare value. Pre-FEAT-36-blocker
# snapshots carry the dict shape and the cascade of 5000+ HTTP
# 400s the user reported originated here.
_ENUM_DICT_KEYS = frozenset({"value", "label"})

# How much of each jsonl file we sample. The first row is always
# smaller than this for renderer-minimum scope; we read up to the
# first newline so the check is O(1) per file.
_SAMPLE_BYTES = 4096

# Audit/output files that are not record streams; the enum-dict
# check must skip them so it does not mis-classify on their
# different shape.
_AUDIT_FILES = frozenset({
    "flags.jsonl", "progress.jsonl", "_deferred.jsonl", "audit.jsonl",
})


@dataclass
class PreflightReport:
    """Aggregate of every pre-flight check's findings."""

    version_skew: VersionSkew = VersionSkew.NONE
    missing_content_types: set[str] = field(default_factory=set)
    # ARCH-08b: content types in the manifest that nbsnap itself does
    # not recognise (i.e. not present in CONTENT_TYPE_FILES). Unlike
    # ``missing_content_types`` (which asks the *destination* whether
    # the type exists), this is a snapshot-side defect: a typo at
    # export time, a corrupted manifest, or a plugin content type
    # that the operator forgot to register.
    unknown_content_types: set[str] = field(default_factory=set)
    missing_custom_fields: set[str] = field(default_factory=set)
    snapshot_format_version: int = 1
    # FEAT-36h: list of `path: field` strings, one per jsonl
    # file whose first row carries the legacy enum-dict shape.
    # Each issue is `{"path": str, "field": str, "rows_affected": int}`.
    # BUG-01a moved this from raw strings to structured dicts;
    # the CLI renders them back to strings for the operator.
    snapshot_format_issues: list[dict[str, Any]] = field(default_factory=list)
    # FEAT-46b: per-(content_type, field) drift between the
    # snapshot's OpenAPI and the destination's. Populated by
    # `run_preflight` when both schemas are available.
    schema_drift: list[Any] = field(default_factory=list)

    def is_blocking(
        self,
        max_skew: VersionSkew,
        *,
        allow_enum_dict_bypass: bool = False,
        strict_schema: bool = False,
    ) -> bool:
        """True iff any check found a blocking condition.

        `allow_enum_dict_bypass` lets the operator override the
        enum-dict refusal when a re-export is not yet possible.
        The import-side `_collapse_enum_dict` coerce should
        still rescue most fields, but the round-trip guarantee
        is gone, so the bypass is documented but not advertised.

        `strict_schema` (FEAT-46c) makes any non-empty
        `schema_drift` block the import. Off by default; the
        diff is informational unless the operator opts in.
        """

        if self.unknown_content_types:
            return True
        if self.missing_content_types:
            return True
        if self.snapshot_format_issues and not allow_enum_dict_bypass:
            return True
        if strict_schema and self.schema_drift:
            return True
        return not self.version_skew.allowed_by(max_skew)


def sample_enum_dict_check(snapshot_dir: Path) -> list[dict[str, Any]]:
    """Walk every row of each jsonl, flag enum-dict shapes.

    Returns one entry per offending file in the shape
    `{"path": str, "field": str, "rows_affected": int}`, sorted
    by path. The `field` is the first enum-dict field seen, which
    is enough for the operator to verify with
    `jq 'select(.body.<field>|type=="object")' <file>`.

    Performance: O(rows) `json.loads` calls. Sub-second on a
    100k-row file because the decoder is the bottleneck.
    BUG-01a: this used to read only the first line, so a
    snapshot with only some legacy rows passed preflight clean.
    """

    issues: list[dict[str, Any]] = []
    for jsonl in sorted(snapshot_dir.rglob("*.jsonl")):
        if jsonl.name in _AUDIT_FILES:
            continue
        first_field: str | None = None
        rows_affected = 0
        with jsonl.open(encoding="utf-8") as fp:
            for raw in fp:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                body = row.get("body") or {}
                if not isinstance(body, dict):
                    continue
                row_has_enum_dict = False
                for field_name, value in body.items():
                    if (
                        isinstance(value, dict)
                        and frozenset(value.keys()) == _ENUM_DICT_KEYS
                    ):
                        if first_field is None:
                            first_field = field_name
                        row_has_enum_dict = True
                        break
                if row_has_enum_dict:
                    rows_affected += 1
        if rows_affected > 0:
            rel = jsonl.relative_to(snapshot_dir).as_posix()
            issues.append({
                "path": rel,
                "field": first_field,
                "rows_affected": rows_affected,
            })
    return issues


def run_preflight(
    http: NetboxHTTP,
    manifest: Manifest,
    *,
    custom_field_names: set[str] | None = None,
    snapshot_dir: Path | None = None,
    snapshot_openapi: Any = None,
) -> PreflightReport:
    """Execute the pre-flight checks against the destination.

    `snapshot_dir`, when provided, enables the FEAT-36h
    enum-dict shape scan. The driver always passes it; callers
    that only want the destination-side checks can omit it.

    `snapshot_openapi`, when provided alongside a reachable
    destination, drives the FEAT-46b schema-drift check.
    Skipped silently if either side is missing.
    """

    report = PreflightReport(snapshot_format_version=manifest.version)

    # ARCH-08b: hard-fail on unknown content types BEFORE touching
    # the network. Walking manifest.counts and calling relative_path
    # lets us collect every unknown content type in a single pass,
    # so the operator gets one consolidated error message rather
    # than discovering them one at a time as the importer crashes
    # on each. Returning early also means a misconfigured manifest
    # cannot waste API calls against the destination.
    unknown: set[str] = set()
    for content_type in manifest.counts:
        try:
            relative_path(content_type)
        except UnknownContentTypeError:
            unknown.add(content_type)
    if unknown:
        report.unknown_content_types = unknown
        return report

    if snapshot_dir is not None:
        report.snapshot_format_issues = sample_enum_dict_check(snapshot_dir)

    # FEAT-46b: schema-drift comparison between the snapshot's
    # OpenAPI and the destination's. The fetch is best-effort;
    # a destination that refuses /api/schema/ stays silent so
    # the diff does not turn the preflight into an outage.
    if snapshot_openapi is not None:
        try:
            from nbsnap.schema.diff import diff_schemas
            from nbsnap.schema.openapi import OpenAPI as _OpenAPI
            dest_schema = _OpenAPI.fetch(http)
            scope = {ct for ct in manifest.counts if isinstance(ct, str)}
            report.schema_drift = diff_schemas(
                snapshot_openapi, dest_schema, scope,
            )
        except Exception:  # noqa: BLE001 - best effort, log only
            import logging
            logging.getLogger(__name__).info(
                "schema-drift check skipped: destination /api/schema/ "
                "unavailable or unreadable"
            )

    # ------------------------------------------------------------------
    # Version skew
    # ------------------------------------------------------------------
    dest_status = Status.fetch(http)
    source_status = Status(
        netbox_version=manifest.netbox_version,
        python_version="unknown",
    )
    report.version_skew = source_status.version_skew(dest_status)

    # ------------------------------------------------------------------
    # Content-type coverage
    # ------------------------------------------------------------------
    cache = ContentTypeCache.fetch(http)
    expected = set(manifest.counts.keys())
    missing: set[str] = set()
    for ct in expected:
        app, _, model = ct.partition(".")
        if not cache.has(app, model):
            missing.add(ct)
    report.missing_content_types = missing

    # ------------------------------------------------------------------
    # Custom-field reconciliation
    # ------------------------------------------------------------------
    if custom_field_names is not None:
        existing: set[str] = set()
        try:
            for row in http.get_all("extras/custom-fields/?limit=200"):
                name = row.get("name")
                if isinstance(name, str):
                    existing.add(name)
        except Exception:  # noqa: BLE001 - missing field is informational
            existing = set()
        report.missing_custom_fields = custom_field_names - existing

    return report
