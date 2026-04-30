"""FEAT-26a snapshot diff tests."""

from __future__ import annotations

import json
from pathlib import Path

from nbsnap.verify.diff import DEFAULT_EXCLUSIONS, diff_trees


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_identical_trees_are_clean(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    rows = [{"natural_key": ["hall-d"], "body": {"name": "Hall D"}}]
    _write_jsonl(a / "dcim/sites.jsonl", rows)
    _write_jsonl(b / "dcim/sites.jsonl", rows)
    result = diff_trees(a, b, DEFAULT_EXCLUSIONS)
    assert result.is_clean()


def test_changed_row_is_surfaced(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_jsonl(
        a / "dcim/sites.jsonl",
        [{"natural_key": ["hall-d"], "body": {"name": "Hall D"}}],
    )
    _write_jsonl(
        b / "dcim/sites.jsonl",
        [{"natural_key": ["hall-d"], "body": {"name": "Hall E"}}],
    )
    result = diff_trees(a, b, DEFAULT_EXCLUSIONS)
    assert not result.is_clean()
    file_diff = next(fd for fd in result.file_diffs if fd.path == "dcim/sites.jsonl")
    assert len(file_diff.rows_changed) == 1


def test_ignored_fields_do_not_count(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_jsonl(
        a / "dcim/sites.jsonl",
        [{"natural_key": ["hall-d"], "body": {"name": "Hall D", "id": 1}}],
    )
    _write_jsonl(
        b / "dcim/sites.jsonl",
        [{"natural_key": ["hall-d"], "body": {"name": "Hall D", "id": 99}}],
    )
    result = diff_trees(a, b, DEFAULT_EXCLUSIONS)
    assert result.is_clean()
