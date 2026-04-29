"""FEAT-05b graph build tests."""

from __future__ import annotations

from nbsnap.graph.build import from_openapi
from nbsnap.graph.model import Node
from nbsnap.schema.openapi import OpenAPI


def _schema() -> dict:
    return {
        "paths": {
            "/api/dcim/sites/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"properties": {"id": {}, "name": {}}}
                                }
                            }
                        }
                    }
                }
            },
            "/api/dcim/devices/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "properties": {
                                            "id": {},
                                            "name": {},
                                            "site": {"title": "BriefSite"},
                                            "primary_ip4": {
                                                "title": "BriefIPAddress",
                                                "nullable": True,
                                            },
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/ipam/ip-addresses/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"properties": {"id": {}, "address": {}}}
                                }
                            }
                        }
                    }
                }
            },
            "/api/extras/object-changes/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "properties": {
                                            "id": {},
                                            "user": {"title": "BriefUser"},
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
        }
    }


def test_device_to_site_edge_exists() -> None:
    openapi = OpenAPI(_schema())
    graph = from_openapi(openapi, scope={"dcim.site", "dcim.device", "ipam.ipaddress"})
    out = graph.out_edges(Node("dcim.device"))
    fields = {edge.field for edge in out}
    assert "site" in fields


def test_device_to_ipaddress_edge_is_nullable() -> None:
    openapi = OpenAPI(_schema())
    graph = from_openapi(openapi, scope={"dcim.site", "dcim.device", "ipam.ipaddress"})
    out = graph.out_edges(Node("dcim.device"))
    primary = next((e for e in out if e.field == "primary_ip4"), None)
    assert primary is not None
    assert primary.nullable is True


def test_out_of_scope_endpoints_are_dropped() -> None:
    openapi = OpenAPI(_schema())
    graph = from_openapi(openapi, scope={"dcim.site", "dcim.device", "ipam.ipaddress"})
    assert Node("extras.objectchange") not in graph
