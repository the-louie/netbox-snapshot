"""Tests for task #21: OUT_OF_SCOPE drops do not emit `dropping FK` warnings.

The audit summary at end-of-run shows out-of-scope drops in
their own bucket, so emitting a separate `dropping FK` warning
per row inside that bucket is just noise. After this commit,
the resolver writes to the audit log but skips the warning
when the category is OUT_OF_SCOPE.

Two behaviours pinned:

1. An OUT_OF_SCOPE drop records to the audit and emits NO
   `dropping FK ...` log line.
2. A MISSING_FROM_SOURCE drop still emits the warning, because
   that signals a real data gap the operator should investigate.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from nbsnap.import_.audit import Auditor, DropCategory
from nbsnap.import_.driver import _resolve_body
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.natkey.registry import default as default_registry
from nbsnap.schema.openapi import OpenAPI


def _site_schema() -> OpenAPI:
    """A schema where dcim.site has a region FK. Mirrors the
    case where region is intentionally out of scope per the
    network-only banner."""

    return OpenAPI(
        {
            "components": {
                "schemas": {
                    "Site": {
                        "type": "object",
                        "properties": {
                            "id": {},
                            "slug": {"type": "string"},
                            "region": {
                                "allOf": [{"$ref": "#/components/schemas/BriefRegion"}],
                                "nullable": True,
                            },
                        },
                    },
                    "PaginatedSiteList": {
                        "properties": {
                            "results": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Site"},
                            }
                        }
                    },
                    "BriefRegion": {
                        "type": "object",
                        "properties": {"id": {}, "slug": {}},
                    },
                }
            },
            "paths": {
                "/api/dcim/sites/": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/PaginatedSiteList"}
                                    }
                                }
                            }
                        }
                    },
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"properties": {"slug": {}, "region": {}}}
                                }
                            }
                        }
                    },
                },
                "/api/dcim/regions/": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"properties": {"id": {}, "slug": {}}}
                                    }
                                }
                            }
                        }
                    },
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {"schema": {"properties": {"slug": {}}}}
                            }
                        }
                    },
                },
            },
        }
    )


def test_out_of_scope_drop_does_not_emit_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Region is not in the snapshot at all, so the resolver
    classifies as OUT_OF_SCOPE and skips the warning. The
    audit picks the event up; stderr stays clean."""

    auditor = Auditor()
    body = {"slug": "hall-d", "region": ["elmia"]}
    http = MagicMock(get_all=MagicMock(return_value=iter([])))

    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        out = _resolve_body(
            "dcim.site",
            body,
            _site_schema(),
            NKIndex(),
            http,
            default_registry(),
            snapshot_index=SnapshotIndex(),  # empty -> OUT_OF_SCOPE
            processing_stack=set(),
            deferred_queue=[],
            current_nk=("hall-d",),
            auditor=auditor,
        )

    # Audit recorded the drop with the correct category.
    assert len(auditor.events) == 1
    assert auditor.events[0].category is DropCategory.OUT_OF_SCOPE

    # No `dropping FK` warning landed on stderr/log.
    drop_warnings = [r for r in caplog.records if "dropping FK" in r.getMessage()]
    assert drop_warnings == []

    # Region field was dropped from the resolved body.
    assert "region" not in out


def test_missing_from_source_drop_still_emits_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the target content type IS in the snapshot but the
    specific NK is missing, the resolver still warns because
    that signals a real data gap. The audit also records it
    in a separate bucket."""

    # Seed the snapshot with a different region NK so the
    # classifier picks MISSING_FROM_SOURCE.
    snapshot_index = SnapshotIndex()
    snapshot_index._by_key[("dcim.region", ("other-region",))] = {"slug": "other-region"}

    auditor = Auditor()
    body = {"slug": "hall-d", "region": ["elmia"]}
    http = MagicMock(get_all=MagicMock(return_value=iter([])))

    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.driver"):
        _resolve_body(
            "dcim.site",
            body,
            _site_schema(),
            NKIndex(),
            http,
            default_registry(),
            snapshot_index=snapshot_index,
            processing_stack=set(),
            deferred_queue=[],
            current_nk=("hall-d",),
            auditor=auditor,
        )

    assert auditor.events[0].category is DropCategory.MISSING_FROM_SOURCE
    # BUG-08: MISSING_FROM_SOURCE drops emit a category-aware
    # warning that points the operator at the source NetBox.
    drop_warnings = [
        r
        for r in caplog.records
        if "source NetBox has a stale or broken reference" in r.getMessage()
    ]
    assert drop_warnings


def test_record_drop_returns_category() -> None:
    """`_record_drop` returns the category it picked so the
    caller can branch on it. Used by all three call sites to
    decide whether to emit the warning."""

    from nbsnap.import_.driver import _record_drop

    auditor = Auditor()
    cat = _record_drop(
        auditor=auditor,
        snapshot_index=SnapshotIndex(),  # empty
        deferred_queue=[],
        queue_size_before=0,
        value=["elmia"],
        child_ct="dcim.site",
        child_nk=("hall-d",),
        field_name="region",
        target_ct="dcim.region",
    )
    assert cat is DropCategory.OUT_OF_SCOPE


def test_record_drop_returns_none_when_auditor_missing() -> None:
    """The no-auditor backwards-compat path returns None
    explicitly, so callers can detect "no classification
    available" and fall back to the legacy warn-everything
    behaviour."""

    from nbsnap.import_.driver import _record_drop

    cat = _record_drop(
        auditor=None,
        snapshot_index=None,
        deferred_queue=None,
        queue_size_before=0,
        value=("x",),
        child_ct="dcim.site",
        child_nk=(),
        field_name="region",
        target_ct="dcim.region",
    )
    assert cat is None
