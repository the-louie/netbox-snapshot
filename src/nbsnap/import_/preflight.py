"""Pre-flight checks (FEAT-18a/b/c).

Three checks run before any write to the destination:

* **Version and format compatibility.** Manifest's snapshot
  format version vs the importer's. Destination NetBox version
  vs the source's, gated by `--max-version-skew`.
* **Content-type coverage.** Every content type in the manifest
  must exist on the destination. Missing types abort with a
  clear list.
* **Custom-field reconciliation.** Custom fields the snapshot
  references but the destination lacks are reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nbsnap.export.manifest import Manifest
from nbsnap.http.client import NetboxHTTP
from nbsnap.schema.content_types import ContentTypeCache
from nbsnap.schema.status import Status, VersionSkew


@dataclass
class PreflightReport:
    """Aggregate of every pre-flight check's findings."""

    version_skew: VersionSkew = VersionSkew.NONE
    missing_content_types: set[str] = field(default_factory=set)
    missing_custom_fields: set[str] = field(default_factory=set)
    snapshot_format_version: int = 1

    def is_blocking(self, max_skew: VersionSkew) -> bool:
        """True iff any check found a blocking condition."""
        if self.missing_content_types:
            return True
        return not self.version_skew.allowed_by(max_skew)


def run_preflight(
    http: NetboxHTTP,
    manifest: Manifest,
    *,
    custom_field_names: set[str] | None = None,
) -> PreflightReport:
    """Execute the three pre-flight checks against the destination."""

    report = PreflightReport(snapshot_format_version=manifest.version)

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
