"""FEAT-14a/b writer tests."""

from __future__ import annotations

import json
from pathlib import Path

from nbsnap.export.extractor import ExtractedRow
from nbsnap.export.writer import write_content_type
from nbsnap.snapshot import relative_path


def test_relative_path_for_known_content_type() -> None:
    assert relative_path("dcim.site") == "dcim/sites.jsonl"


def test_write_content_type_sorts_by_natural_key(tmp_path: Path) -> None:
    rows = [
        ExtractedRow("dcim.site", ("zeta",), {"name": "Zeta"}),
        ExtractedRow("dcim.site", ("alpha",), {"name": "Alpha"}),
    ]
    count = write_content_type(tmp_path, "dcim.site", rows)
    assert count == 2
    file_path = tmp_path / "dcim/sites.jsonl"
    text = file_path.read_text().splitlines()
    first = json.loads(text[0])
    assert first["natural_key"] == ["alpha"]


def test_write_content_type_replaces_existing_file(tmp_path: Path) -> None:
    """Re-writing the same content type overwrites cleanly."""

    write_content_type(tmp_path, "dcim.site", [ExtractedRow("dcim.site", ("a",), {})])
    write_content_type(tmp_path, "dcim.site", [ExtractedRow("dcim.site", ("b",), {})])
    text = (tmp_path / "dcim/sites.jsonl").read_text().splitlines()
    assert len(text) == 1
    assert json.loads(text[0])["natural_key"] == ["b"]
