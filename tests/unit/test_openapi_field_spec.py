"""FEAT-02c field_spec tests."""

from __future__ import annotations

from nbsnap.schema.openapi import OpenAPI


def _schema_with_fk() -> dict:
    """OpenAPI fragment that includes a `dcim.device.site` FK and tags m2m."""

    return {
        "paths": {
            "/api/dcim/sites/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        }
                    }
                },
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"properties": {"name": {}}}}}
                    }
                },
            },
            "/api/ipam/ip-addresses/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "address": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        }
                    }
                },
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"properties": {"address": {}}}}}
                    }
                },
            },
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
                                            "name": {"type": "string"},
                                            "primary_ip4": {
                                                "title": "BriefIPAddress",
                                                "type": "object",
                                                "nullable": True,
                                            },
                                            "tags": {
                                                "type": "array",
                                                "items": {"title": "BriefTag"},
                                            },
                                        },
                                        "required": ["name"],
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
                                        "name": {},
                                        "primary_ip4": {},
                                        "tags": {"type": "array"},
                                    },
                                    "required": ["name"],
                                }
                            }
                        }
                    }
                },
            },
            "/api/extras/tags/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {}, "name": {}},
                                    }
                                }
                            }
                        }
                    }
                },
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"properties": {"name": {}}}}}
                    }
                },
            },
        }
    }


def test_field_spec_detects_fk_target_via_title() -> None:
    """`primary_ip4` carries `title=BriefIPAddress`, resolves to `ipam.ipaddress`."""

    openapi = OpenAPI(_schema_with_fk())
    spec = openapi.field_spec("dcim.device", "primary_ip4")
    assert spec.nullable is True
    assert spec.fk_target == "ipam.ipaddress"
    assert spec.is_m2m is False


def test_field_spec_detects_m2m_via_array() -> None:
    """`tags` is `type=array`, marked m2m."""

    openapi = OpenAPI(_schema_with_fk())
    spec = openapi.field_spec("dcim.device", "tags")
    assert spec.is_m2m is True


def test_field_spec_read_only_field_has_no_write_flag() -> None:
    """`id` is in GET only, not write-allowed."""

    openapi = OpenAPI(_schema_with_fk())
    spec = openapi.field_spec("dcim.device", "id")
    assert spec.write_allowed is False


def test_field_spec_required_field_has_required_flag() -> None:
    """`name` is in `required`, flag fires."""

    openapi = OpenAPI(_schema_with_fk())
    spec = openapi.field_spec("dcim.device", "name")
    assert spec.required is True
