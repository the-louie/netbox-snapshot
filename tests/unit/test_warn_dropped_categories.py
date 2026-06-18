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
from nbsnap.import_.driver import _warn_dropped


@pytest.fixture()
def warn_dedup() -> set[tuple[str, str, str]]:
    """A fresh per-test dedup set, mirroring what
    `ImportSummary._warned_missing_fk` carries during a real
    run. REFACTOR-08 moved the dedup off the module global."""

    return set()


def test_missing_from_source_warning_points_at_source(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _warn_dropped(
            "dcim.device",
            "site",
            "dcim.site",
            KeyError("nope"),
            category=DropCategory.MISSING_FROM_SOURCE,
        )
    msg = caplog.messages[-1]
    assert "source NetBox" in msg
    assert "stale or broken reference" in msg
    assert "dcim.device" in msg and "site" in msg


def test_upsert_failed_warning_points_at_destination(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _warn_dropped(
            "dcim.device",
            "site",
            "dcim.site",
            RuntimeError("HTTP 400"),
            category=DropCategory.UPSERT_FAILED,
        )
    msg = caplog.messages[-1]
    assert "destination NetBox refused" in msg
    assert "audit log" in msg


def test_unknown_category_keeps_legacy_text(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _warn_dropped(
            "dcim.device",
            "site",
            "dcim.site",
            KeyError("nope"),
            category=None,
        )
    msg = caplog.messages[-1]
    assert msg.startswith("dropping FK dcim.device.site")


def test_warning_is_emitted_once_per_triple(caplog, warn_dedup) -> None:
    """Dedup keyed on (ct, field, target) so flooding the log
    with the same warning is impossible even across categories.
    REFACTOR-08: the dedup set is per-call (and per-summary in a
    real run); the caller passes it explicitly."""

    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _warn_dropped(
            "dcim.device",
            "site",
            "dcim.site",
            KeyError("nope"),
            category=DropCategory.MISSING_FROM_SOURCE,
            warn_dedup=warn_dedup,
        )
        _warn_dropped(
            "dcim.device",
            "site",
            "dcim.site",
            KeyError("nope"),
            category=DropCategory.UPSERT_FAILED,
            warn_dedup=warn_dedup,
        )
    assert len(caplog.messages) == 1


def test_two_summaries_dedup_independently(caplog) -> None:
    """REFACTOR-08 regression: two `run_import` calls (modelled
    here as two distinct dedup sets) both emit the first
    warning. Before this change the second call inherited the
    first's suppressions because the dedup was a module global."""

    set_a: set[tuple[str, str, str]] = set()
    set_b: set[tuple[str, str, str]] = set()
    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _warn_dropped(
            "dcim.device",
            "site",
            "dcim.site",
            KeyError("nope"),
            category=DropCategory.MISSING_FROM_SOURCE,
            warn_dedup=set_a,
        )
        _warn_dropped(
            "dcim.device",
            "site",
            "dcim.site",
            KeyError("nope"),
            category=DropCategory.MISSING_FROM_SOURCE,
            warn_dedup=set_b,
        )
    # Each set saw the triple for the first time, both warnings
    # fired.
    assert len(caplog.messages) == 2
