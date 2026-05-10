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


def _netbox_4_6_paginated_with_refs() -> dict:
    """NetBox 4.6 shape: list endpoint returns PaginatedXList, fields use $ref."""
    return {
        "components": {
            "schemas": {
                "PaginatedDeviceList": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "results": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/DeviceWithConfigContext"},
                        },
                    },
                },
                "DeviceWithConfigContext": {
                    "type": "object",
                    "required": ["name", "site", "role"],
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "site": {"$ref": "#/components/schemas/BriefSite"},
                        "role": {"$ref": "#/components/schemas/BriefDeviceRole"},
                        "primary_ip4": {
                            "allOf": [{"$ref": "#/components/schemas/BriefIPAddress"}],
                            "nullable": True,
                        },
                        "tags": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/NestedTag"},
                        },
                    },
                },
                "BriefSite": {"type": "object", "properties": {"id": {}, "slug": {}}},
                "BriefDeviceRole": {"type": "object", "properties": {"id": {}, "slug": {}}},
                "BriefIPAddress": {"type": "object", "properties": {"id": {}, "address": {}}},
                "NestedTag": {"type": "object", "properties": {"id": {}, "slug": {}}},
            }
        },
        "paths": {
            "/api/dcim/sites/": {
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
                }
            },
            "/api/dcim/device-roles/": {
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
            "/api/extras/tags/": {
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
                }
            },
            "/api/dcim/devices/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/PaginatedDeviceList"
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
                                        "name": {},
                                        "site": {},
                                        "role": {},
                                        "primary_ip4": {},
                                        "tags": {},
                                    }
                                }
                            }
                        }
                    }
                },
            },
        },
    }


def test_fk_detection_descends_into_paginated_list_response() -> None:
    """List endpoint returns PaginatedXList wrapper; unwrap to find FKs."""

    openapi = OpenAPI(_netbox_4_6_paginated_with_refs())
    site_spec = openapi.field_spec("dcim.device", "site")
    assert site_spec.fk_target == "dcim.site"


def test_fk_detection_handles_allof_wrapper_for_nullable_fks() -> None:
    """`primary_ip4` uses `allOf: [{$ref}]` plus `nullable: true`."""

    openapi = OpenAPI(_netbox_4_6_paginated_with_refs())
    spec = openapi.field_spec("dcim.device", "primary_ip4")
    assert spec.fk_target == "ipam.ipaddress"
    assert spec.nullable is True


def test_fk_detection_handles_m2m_array_with_ref_items() -> None:
    """`tags` is `type: array, items: {$ref: ...}`."""

    openapi = OpenAPI(_netbox_4_6_paginated_with_refs())
    spec = openapi.field_spec("dcim.device", "tags")
    assert spec.is_m2m is True
    assert spec.fk_target == "extras.tag"
