"""Tests for task #22: look-ahead path resolves the body before upsert.

Before the fix, `resolve_or_create` called `upsert(body=dict(snapshot_body))`
with the raw snapshot body. FK fields stayed in NK-tuple/list form so
NetBox refused the POST with HTTP 400 (`manufacturer: ['debian']` is not
a valid integer FK).

After the fix, `resolve_or_create` routes the body through the driver's
`_resolve_body` so every FK lands as a resolved destination id before
the POST fires. The recursion still works because `_resolve_body`'s own
look-ahead callout participates in the same `processing_stack`.

Three behaviours pinned here:

1. When `openapi` is wired in, the look-ahead POST carries
   resolved FKs (integer ids), not raw NK lists.
2. When `openapi` is omitted, the backwards-compat path still
   posts the raw body, matching the pre-fix behaviour so older
   callers do not break.
3. The recursion through `_resolve_body` does not loop on itself
   because the processing-stack key is added BEFORE the resolver
   walks the body.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.lookahead import resolve_or_create
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.natkey.registry import default as default_registry
from nbsnap.schema.openapi import OpenAPI


def _platform_schema() -> OpenAPI:
    """A minimal schema where dcim.platform has an FK to
    dcim.manufacturer. Lets us assert the resolver replaces
    ['debian'] with the integer id at POST time."""

    return OpenAPI(
        {
            "components": {
                "schemas": {
                    "Platform": {
                        "type": "object",
                        "properties": {
                            "id": {},
                            "slug": {"type": "string"},
                            "manufacturer": {"$ref": "#/components/schemas/BriefManufacturer"},
                        },
                    },
                    "PaginatedPlatformList": {
                        "properties": {
                            "results": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Platform"},
                            }
                        }
                    },
                    "BriefManufacturer": {
                        "type": "object",
                        "properties": {"id": {}, "slug": {}},
                    },
                }
            },
            "paths": {
                "/api/dcim/platforms/": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/PaginatedPlatformList"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "properties": {
                                            "slug": {},
                                            "manufacturer": {},
                                        }
                                    }
                                }
                            }
                        }
                    },
                },
                "/api/dcim/manufacturers/": {
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


def test_lookahead_posts_resolved_fk_when_openapi_provided() -> None:
    """The headline assertion for task #22: with `openapi` wired
    in, the POST body carries the resolved manufacturer id, not
    the raw `["debian"]` NK list."""

    http = MagicMock()
    http.get_all.return_value = iter([])  # destination empty
    # Manufacturer POST returns id=7; platform POST returns id=42.
    http.post.side_effect = [
        {"id": 7, "slug": "debian"},
        {"id": 42, "slug": "debian13-trixie"},
    ]

    snapshot_index = SnapshotIndex()
    snapshot_index._by_key[("dcim.manufacturer", ("debian",))] = {
        "name": "Debian",
        "slug": "debian",
    }
    snapshot_index._by_key[("dcim.platform", ("debian13-trixie",))] = {
        "name": "Debian13 Trixie",
        "slug": "debian13-trixie",
        "manufacturer": ["debian"],  # NK-list form, the bug case
    }

    rid = resolve_or_create(
        http,
        snapshot_index,
        NKIndex(),
        default_registry(),
        content_type="dcim.platform",
        natural_key=("debian13-trixie",),
        processing_stack=set(),
        deferred_queue=[],
        openapi=_platform_schema(),
    )
    assert rid == 42

    # Two POSTs fired: manufacturer first, then platform.
    assert http.post.call_count == 2
    # `upsert.py` always invokes `http.post(endpoint, body)`
    # positionally, so the body lives at args[1]. Asserting
    # against the positional slot keeps the test honest about
    # the contract; if upsert ever changes to kwargs the test
    # should fail loudly so we can fix both sides together.
    platform_call = http.post.call_args_list[-1]
    posted_body = platform_call.args[1]

    # The fix: `manufacturer` resolves to the integer id 7, not
    # the raw NK list. The pre-fix code would have left
    # ['debian'] in the body.
    assert posted_body["manufacturer"] == 7


def test_lookahead_without_openapi_falls_back_to_raw_body() -> None:
    """Backwards-compat: a caller that omits `openapi` gets the
    pre-fix behaviour. Useful for existing unit tests that wire
    `resolve_or_create` without the full driver context."""

    http = MagicMock()
    http.get_all.return_value = iter([])
    http.post.return_value = {"id": 42, "slug": "site-a"}

    snapshot_index = SnapshotIndex()
    snapshot_index._by_key[("dcim.site", ("hall-d",))] = {
        "name": "Hall-D",
        "slug": "hall-d",
    }

    rid = resolve_or_create(
        http,
        snapshot_index,
        NKIndex(),
        default_registry(),
        content_type="dcim.site",
        natural_key=("hall-d",),
        processing_stack=set(),
        deferred_queue=[],
        # no openapi -> raw body path
    )
    assert rid == 42
    http.post.assert_called_once()


def test_lookahead_recursion_does_not_loop_via_resolve_body() -> None:
    """When `_resolve_body` calls `_try_lookahead`, which calls
    `resolve_or_create` again, the cycle guard in
    `processing_stack` must prevent infinite recursion. The key
    for the CURRENT target is added BEFORE the body resolver
    runs."""

    http = MagicMock()
    http.get_all.return_value = iter([])
    http.post.return_value = {"id": 99, "slug": "self-ref"}

    snapshot_index = SnapshotIndex()
    # A record whose FK loops back to itself (e.g. a hypothetical
    # platform.parent pointing at the same platform). The body
    # resolver would call back into resolve_or_create with the
    # same key; processing_stack must short-circuit it.
    snapshot_index._by_key[("dcim.platform", ("self-ref",))] = {
        "name": "Self",
        "slug": "self-ref",
        "parent": ["self-ref"],
    }

    stack: set = set()
    queue: list = []

    rid = resolve_or_create(
        http,
        snapshot_index,
        NKIndex(),
        default_registry(),
        content_type="dcim.platform",
        natural_key=("self-ref",),
        processing_stack=stack,
        deferred_queue=queue,
        openapi=_platform_schema(),
    )
    # The outer create succeeds with id=99; the inner self-loop
    # got short-circuited via the processing_stack guard and the
    # FK was dropped (or deferred via the queue).
    assert rid == 99
    # Exactly ONE POST. If the recursion had looped it would
    # have re-entered resolve_or_create for the same key and
    # tried to POST a second time.
    assert http.post.call_count == 1
    # And the stack is clean afterwards.
    assert ("dcim.platform", ("self-ref",)) not in stack
