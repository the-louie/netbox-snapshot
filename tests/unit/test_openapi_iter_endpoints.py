"""FEAT-02b iter_endpoints tests."""

from __future__ import annotations

from nbsnap.schema.openapi import OpenAPI, _derive_content_type


def test_url_convention_singularises_plural() -> None:
    """`<app>/<plural>/` becomes `<app>.<singular>`."""

    assert _derive_content_type("/api/dcim/sites/") == "dcim.site"
    assert _derive_content_type("/api/dcim/manufacturers/") == "dcim.manufacturer"


def test_curated_entries_win_over_convention() -> None:
    """The curated table overrides the URL convention."""

    assert _derive_content_type("/api/ipam/ip-addresses/") == "ipam.ipaddress"
    assert _derive_content_type("/api/dcim/device-roles/") == "dcim.devicerole"


def test_iter_endpoints_buckets_by_method() -> None:
    """A path with GET + POST yields a single Endpoint with both methods."""

    raw = {
        "paths": {
            "/api/dcim/sites/": {
                "get": {"responses": {"200": {"description": "ok"}}},
                "post": {"requestBody": {}},
            },
            "/non/api/path/": {"get": {}},  # filtered out
        }
    }
    openapi = OpenAPI(raw)
    endpoints = list(openapi.iter_endpoints())
    assert len(endpoints) == 1
    e = endpoints[0]
    assert e.content_type == "dcim.site"
    assert set(e.methods.keys()) == {"GET", "POST"}


def test_resolve_ref_traverses_components() -> None:
    """`_resolve_ref` walks the JSON pointer."""

    raw = {
        "paths": {},
        "components": {
            "schemas": {
                "Site": {"type": "object", "properties": {"name": {"type": "string"}}},
            }
        },
    }
    openapi = OpenAPI(raw)
    resolved = openapi._resolve_ref("#/components/schemas/Site")
    assert resolved["type"] == "object"
