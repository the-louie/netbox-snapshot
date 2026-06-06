"""ARCH-08b: preflight rejects manifests with unknown content types.

The check runs BEFORE any HTTP call: a misconfigured snapshot
must not be allowed to waste API budget against the destination
before failing. We verify both the report shape (the unknown set
is populated) and the early-return contract (no Status.fetch or
ContentTypeCache.fetch was issued).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.preflight import PreflightReport, run_preflight
from nbsnap.snapshot.manifest import Manifest


def _manifest_with_typo() -> Manifest:
    return Manifest(
        netbox_version="4.6.2",
        counts={"dcim.site": 1, "dcim.devic": 7},  # the typo
    )


def test_preflight_flags_unknown_content_type() -> None:
    """The unknown CT lands in the report's ``unknown_content_types``."""

    http = MagicMock()
    report = run_preflight(http, _manifest_with_typo())

    assert report.unknown_content_types == {"dcim.devic"}


def test_preflight_skips_network_when_unknown_ct_found() -> None:
    """No GET goes out when an unknown CT is detected.

    A misconfigured snapshot must not cost the destination any API
    calls. Pin that contract here so an over-eager refactor cannot
    drop the early return.
    """

    http = MagicMock()
    run_preflight(http, _manifest_with_typo())

    # No method on the HTTP client should have been called.
    assert not http.get_all.called
    assert not http.get_one.called


def test_unknown_content_types_is_blocking() -> None:
    """``PreflightReport.is_blocking`` returns True on an unknown CT.

    The driver consumes ``is_blocking`` to decide whether to abort
    before invoking the import phases.
    """

    from nbsnap.schema.status import VersionSkew

    report = PreflightReport(unknown_content_types={"dcim.devic"})
    assert report.is_blocking(max_skew=VersionSkew.PATCH) is True


def test_unknown_content_types_starts_empty() -> None:
    """A freshly built ``PreflightReport`` has an empty unknown set.

    Pin the default rather than driving ``run_preflight`` through
    its downstream HTTP path; that path needs Status.fetch and
    ContentTypeCache.fetch to return well-shaped values and the
    setup belongs in a broader integration test, not in this
    ARCH-08b regression.
    """

    report = PreflightReport()
    assert report.unknown_content_types == set()
