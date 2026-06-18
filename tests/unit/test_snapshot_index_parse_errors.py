"""BUG-06 tests for parse-error capture in iter_jsonl.

Pins three behaviours:

1. Without an `errors` list, malformed rows are still skipped but
   a WARNING log entry is emitted (so the operator at least sees
   one breadcrumb).
2. With an `errors` list, every parse failure appends a structured
   entry carrying the path, line number, and decoder message.
3. `SnapshotIndex.from_snapshot` threads the `errors` list through
   `iter_jsonl`, so parse failures in look-ahead-only files
   surface on the same list.
"""

from __future__ import annotations

import logging
from pathlib import Path

from nbsnap.import_.snapshot_index import SnapshotIndex, iter_jsonl


def test_iter_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    """Blank lines are not parse errors and stay silent."""

    p = tmp_path / "rows.jsonl"
    p.write_text('{"a": 1}\n\n   \n{"b": 2}\n')
    errors: list[dict] = []
    rows = list(iter_jsonl(p, errors=errors))
    assert rows == [{"a": 1}, {"b": 2}]
    assert errors == []


def test_iter_jsonl_records_parse_error(tmp_path: Path) -> None:
    """A malformed line appends a `{path, lineno, message}` entry."""

    p = tmp_path / "rows.jsonl"
    p.write_text('{"ok": 1}\n{this is not json\n{"ok2": 1}\n')
    errors: list[dict] = []
    rows = list(iter_jsonl(p, errors=errors))
    assert rows == [{"ok": 1}, {"ok2": 1}]
    assert len(errors) == 1
    entry = errors[0]
    assert entry["path"] == str(p)
    assert entry["lineno"] == 2
    assert "json" in entry["message"].lower() or entry["message"]


def test_iter_jsonl_logs_warning_without_errors_list(tmp_path: Path, caplog) -> None:
    """Even when no `errors` list is supplied, the operator gets
    a WARNING log line for the bad row."""

    p = tmp_path / "rows.jsonl"
    p.write_text('{"ok": 1}\nnot json\n')
    with caplog.at_level(logging.WARNING, logger="nbsnap.import_.snapshot_index"):
        rows = list(iter_jsonl(p))
    assert rows == [{"ok": 1}]
    assert any("parse error" in m for m in caplog.messages)


def test_snapshot_index_threads_errors(tmp_path: Path) -> None:
    """A parse failure during `SnapshotIndex.from_snapshot` lands
    on the shared `errors` list, not the floor."""

    # Build the minimum directory structure that the loader walks:
    # one recognised CONTENT_TYPE_FILES path so the row is parsed.
    sites_dir = tmp_path / "dcim"
    sites_dir.mkdir()
    sites_file = sites_dir / "sites.jsonl"
    sites_file.write_text('{"natural_key": ["hall-a"], "body": {"name": "hall-a"}}\n{broken\n')

    errors: list[dict] = []
    index = SnapshotIndex.from_snapshot(tmp_path, errors=errors)
    assert len(index) == 1
    assert len(errors) == 1
    assert errors[0]["lineno"] == 2
