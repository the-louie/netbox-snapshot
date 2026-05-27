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

from nbsnap.export.manifest import Manifest
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
    missing_custom_fields: set[str] = field(default_factory=set)
    snapshot_format_version: int = 1
    # FEAT-36h: list of `path: field` strings, one per jsonl
    # file whose first row carries the legacy enum-dict shape.
    snapshot_format_issues: list[str] = field(default_factory=list)

    def is_blocking(
        self,
        max_skew: VersionSkew,
        *,
        allow_enum_dict_bypass: bool = False,
    ) -> bool:
        """True iff any check found a blocking condition.

        `allow_enum_dict_bypass` lets the operator override the
        enum-dict refusal when a re-export is not yet possible.
        The import-side `_collapse_enum_dict` coerce should
        still rescue most fields, but the round-trip guarantee
        is gone, so the bypass is documented but not advertised.
        """

        if self.missing_content_types:
            return True
        if self.snapshot_format_issues and not allow_enum_dict_bypass:
            return True
        return not self.version_skew.allowed_by(max_skew)


def sample_enum_dict_check(snapshot_dir: Path) -> list[str]:
    """Sample the first row of each jsonl, flag enum-dict shapes.

    Returns one human-readable string per offending file. Each
    string carries the relative path and the first field name
    that exhibits the `{value, label}` shape so the operator can
    verify with `head -1 <file> | jq`.

    Performance: O(files) reads of at most `_SAMPLE_BYTES` each.
    The renderer-minimum snapshot has fewer than 30 jsonl files
    so the total cost is well under 10 ms even on a cold cache.
    """

    issues: list[str] = []
    for jsonl in sorted(snapshot_dir.rglob("*.jsonl")):
        if jsonl.name in _AUDIT_FILES:
            continue
        with jsonl.open(encoding="utf-8") as fp:
            line = fp.readline(_SAMPLE_BYTES)
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        body = row.get("body") or {}
        if not isinstance(body, dict):
            continue
        for field_name, value in body.items():
            if (
                isinstance(value, dict)
                and frozenset(value.keys()) == _ENUM_DICT_KEYS
            ):
                rel = jsonl.relative_to(snapshot_dir).as_posix()
                issues.append(
                    f"{rel}: field {field_name!r} carries the "
                    f"{{value, label}} enum-dict shape; the snapshot "
                    f"was exported before FEAT-36-blocker landed"
                )
                # One issue per file is enough; the operator
                # only needs to know the snapshot is bad, the
                # exact field is just a witness.
                break
    return issues


def run_preflight(
    http: NetboxHTTP,
    manifest: Manifest,
    *,
    custom_field_names: set[str] | None = None,
    snapshot_dir: Path | None = None,
) -> PreflightReport:
    """Execute the four pre-flight checks against the destination.

    `snapshot_dir`, when provided, enables the FEAT-36h
    enum-dict shape scan. The driver always passes it; callers
    that only want the destination-side checks can omit it.
    """

    report = PreflightReport(snapshot_format_version=manifest.version)

    if snapshot_dir is not None:
        report.snapshot_format_issues = sample_enum_dict_check(snapshot_dir)

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
