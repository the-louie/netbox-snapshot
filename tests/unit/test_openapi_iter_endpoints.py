"""FEAT-02b iter_endpoints tests."""

from __future__ import annotations

from nbsnap.schema.openapi import OpenAPI, _derive_content_type


def test_url_convention_singularises_plural() -> None:
    """`<app>/<plural>/` becomes `<app>.<singular>`."""

    assert _derive_content_type("/api/dcim/sites/") == "dcim.site"
    assert _derive_content_type("/api/dcim/manufacturers/") == "dcim.manufacturer"


def test_singulariser_handles_sibilant_endings() -> None:
    """`-xes`, `-ches`, `-shes`, `-zzes`, `-sses` strip the full 'es'.

    Regression test for the v4.6 production bug, `prefixes -> prefixe`
    used to silently break every Prefix's NK resolution.
    """

    from nbsnap.schema.openapi import _singularise

    assert _singularise("prefixes") == "prefix"
    assert _singularise("boxes") == "box"
    assert _singularise("branches") == "branch"
    assert _singularise("dishes") == "dish"
    assert _singularise("buzzes") == "buzz"
    assert _singularise("addresses") == "address"


def test_singulariser_strips_only_s_for_soft_e_endings() -> None:
    """`-ses`, `-ges`, `-ces`, `-ves` strip just the 's'."""

    from nbsnap.schema.openapi import _singularise

    assert _singularise("leases") == "lease"
    assert _singularise("ranges") == "range"
    assert _singularise("services") == "service"
    assert _singularise("devices") == "device"


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
