"""FEAT-02a OpenAPI IO tests."""

from __future__ import annotations

from pathlib import Path

from nbsnap.schema.openapi import SCHEMA_PATH, OpenAPI


def _tiny_schema() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "tiny", "version": "1"},
        "paths": {
            "/api/dcim/devices/": {
                "get": {"responses": {"200": {"description": "ok"}}},
            }
        },
    }


def test_dump_and_load_round_trip(tmp_path: Path) -> None:
    """Dump then load reproduces the same parsed schema."""

    raw = _tiny_schema()
    path = tmp_path / "openapi.json"
    OpenAPI(raw).dump(path)
    loaded = OpenAPI.load(path)
    assert loaded.raw == raw


def test_hash_is_stable_across_roundtrip(tmp_path: Path) -> None:
    """Hash matches across a dump/load cycle."""

    raw = _tiny_schema()
    a = OpenAPI(raw)
    a.dump(tmp_path / "a.json")
    b = OpenAPI.load(tmp_path / "a.json")
    assert a.hash() == b.hash()


def test_schema_path_constant_is_layout_value() -> None:
    """The snapshot writer relies on this constant."""

    assert SCHEMA_PATH == "schema/openapi.json"
