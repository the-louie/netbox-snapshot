"""Smoke test, exists so CI has something to assert during early phases.

Later phases pile real tests on top. The version assertion is also
useful as the canary for an accidental version bump that misses
PLAN.md or the CHANGELOG.
"""

from __future__ import annotations

import nbsnap


def test_package_version_matches_expected() -> None:
    """The package version is the documented Phase-0 value."""

    assert nbsnap.__version__ == "0.0.1"
