"""FEAT-02d write allowlist tests."""

from __future__ import annotations

from nbsnap.schema.openapi import OpenAPI


def _device_schema() -> dict:
    """Build a minimal OpenAPI fragment for `dcim.device`."""

    return {
        "paths": {
            "/api/dcim/devices/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "url": {"type": "string"},
                                            "display": {"type": "string"},
                                            "name": {"type": "string"},
                                            "site": {"type": "object"},
                                            "role": {"type": "object"},
                                            "created": {"type": "string"},
                                            "last_updated": {"type": "string"},
                                        },
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
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "site": {"type": "object"},
                                        "role": {"type": "object"},
                                    },
                                    "required": ["name", "site", "role"],
                                }
                            }
                        }
                    }
                },
                "patch": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "site": {"type": "object"},
                                    },
                                }
                            }
                        }
                    }
                },
            }
        }
    }


def test_write_allowlist_unions_post_and_patch() -> None:
    """write_allowlist = post_allowlist ∪ patch_allowlist."""

    openapi = OpenAPI(_device_schema())
    assert openapi.write_allowlist("dcim.device") == frozenset({"name", "site", "role"})


def test_read_only_fields_excludes_writable() -> None:
    """Fields present only in the GET response are read-only."""

    openapi = OpenAPI(_device_schema())
    read_only = openapi.read_only_fields("dcim.device")
    assert "id" in read_only
    assert "url" in read_only
    assert "display" in read_only
    assert "created" in read_only
    assert "last_updated" in read_only
    assert "name" not in read_only


def test_post_and_patch_allowlists_are_separate() -> None:
    """post_allowlist and patch_allowlist can differ."""

    openapi = OpenAPI(_device_schema())
    assert openapi.post_allowlist("dcim.device") == frozenset({"name", "site", "role"})
    assert openapi.patch_allowlist("dcim.device") == frozenset({"name", "site"})
