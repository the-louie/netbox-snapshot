"""FEAT-46a tests for the schema-diff helper."""

from __future__ import annotations

from nbsnap.schema.diff import FieldDrift, diff_schemas
from nbsnap.schema.openapi import OpenAPI


def _two_field_schema(target_for_region: str) -> OpenAPI:
    """A schema where `dcim.site.region` points at the given
    target content type, parameterised so we can build two
    schemas that differ only on that field."""

    return OpenAPI(
        {
            "components": {
                "schemas": {
                    "Site": {
                        "type": "object",
                        "properties": {
                            "id": {},
                            "name": {"type": "string"},
                            "region": {
                                "allOf": [{"$ref": f"#/components/schemas/{target_for_region}"}],
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
                    "BriefArea": {
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
                                    "schema": {"properties": {"name": {}, "region": {}}}
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
                "/api/dcim/areas/": {
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


def test_identical_schemas_produce_no_drift() -> None:
    """Two identical schemas yield zero FieldDrift entries."""

    snap = _two_field_schema("BriefRegion")
    dest = _two_field_schema("BriefRegion")
    assert diff_schemas(snap, dest, {"dcim.site"}) == []


def test_different_fk_target_produces_one_drift() -> None:
    """A single field whose FK target changed between schemas
    yields one FieldDrift entry."""

    snap = _two_field_schema("BriefRegion")
    dest = _two_field_schema("BriefArea")
    drift = diff_schemas(snap, dest, {"dcim.site"})
    region_drifts = [d for d in drift if d.field == "region"]
    assert len(region_drifts) == 1
    d = region_drifts[0]
    assert d.content_type == "dcim.site"
    assert d.snapshot_shape != d.destination_shape
