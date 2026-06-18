"""FEAT-11a-d extractor pipeline tests."""

from __future__ import annotations

from nbsnap.export.extractor import extract
from nbsnap.natkey.registry import default as default_registry
from nbsnap.schema.openapi import OpenAPI


def _minimal_openapi() -> OpenAPI:
    return OpenAPI(
        {
            "paths": {
                "/api/dcim/sites/": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "properties": {
                                                "id": {},
                                                "name": {},
                                                "slug": {},
                                            }
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
                                    "schema": {"properties": {"name": {}, "slug": {}}}
                                }
                            }
                        }
                    },
                }
            }
        }
    )


def test_extractor_drops_read_only_fields() -> None:
    openapi = _minimal_openapi()
    rows = list(
        extract(
            "dcim.site",
            iter([{"id": 5, "name": "Hall D", "slug": "hall-d"}]),
            openapi=openapi,
            registry=default_registry(),
            parent_lookup={},
            source_url="https://prod/",
        )
    )
    extracted, flag = rows[0]
    assert flag is None
    assert extracted is not None
    # id is read-only, should not survive
    assert "id" not in extracted.body
    assert extracted.body["name"] == "Hall D"
    assert extracted.natural_key == ("hall-d",)


def test_extractor_marks_install_local_dns_name() -> None:
    """An IPAddress with dns_name matching the source host is flagged."""

    openapi = OpenAPI(
        {
            "paths": {
                "/api/ipam/ip-addresses/": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "properties": {
                                                "id": {},
                                                "address": {},
                                                "dns_name": {},
                                            }
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
                                            "address": {},
                                            "dns_name": {},
                                            "assigned_object_type": {},
                                            "assigned_object_id": {},
                                        }
                                    }
                                }
                            }
                        }
                    },
                }
            }
        }
    )
    rows = list(
        extract(
            "ipam.ipaddress",
            iter(
                [
                    {
                        "id": 1,
                        "address": "10.0.0.1/32",
                        "dns_name": "prod.example",
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": 1,
                    }
                ]
            ),
            openapi=openapi,
            registry=default_registry(),
            parent_lookup={},
            source_url="https://prod.example/",
        )
    )
    extracted, flag = rows[0]
    assert extracted is None
    assert flag is not None
    assert flag.field == "dns_name"
