"""BUG-08: `_warn_dropped` text varies by DropCategory.

Pins three message templates so an operator who reads the
warning lands on the right NetBox to investigate:

* MISSING_FROM_SOURCE points at the source NetBox.
* UPSERT_FAILED points at the destination NetBox.
* Any other (or None) keeps the legacy "dropping FK" line.
"""

from __future__ import annotations

import logging

import pytest

from nbsnap.import_.audit import DropCategory
from nbsnap.import_.driver import _WARNED_MISSING_FK, _warn_dropped


@pytest.fixture(autouse=True)
def _reset_dedup() -> None:
    _WARNED_MISSING_FK.clear()
    yield
    _WARNED_MISSING_FK.clear()


def test_missing_from_source_warning_points_at_source(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _warn_dropped(
            "dcim.device", "site", "dcim.site", KeyError("nope"),
            category=DropCategory.MISSING_FROM_SOURCE,
        )
    msg = caplog.messages[-1]
    assert "source NetBox" in msg
    assert "stale or broken reference" in msg
    assert "dcim.device" in msg and "site" in msg


def test_upsert_failed_warning_points_at_destination(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _warn_dropped(
            "dcim.device", "site", "dcim.site", RuntimeError("HTTP 400"),
            category=DropCategory.UPSERT_FAILED,
        )
    msg = caplog.messages[-1]
    assert "destination NetBox refused" in msg
    assert "audit log" in msg


def test_unknown_category_keeps_legacy_text(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _warn_dropped(
            "dcim.device", "site", "dcim.site", KeyError("nope"),
            category=None,
        )
    msg = caplog.messages[-1]
    assert msg.startswith("dropping FK dcim.device.site")


def test_warning_is_emitted_once_per_triple(caplog) -> None:
    """Dedup keyed on (ct, field, target) so flooding the log
    with the same warning is impossible even across categories."""

    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _warn_dropped(
            "dcim.device", "site", "dcim.site", KeyError("nope"),
            category=DropCategory.MISSING_FROM_SOURCE,
        )
        _warn_dropped(
            "dcim.device", "site", "dcim.site", KeyError("nope"),
            category=DropCategory.UPSERT_FAILED,
        )
    assert len(caplog.messages) == 1
