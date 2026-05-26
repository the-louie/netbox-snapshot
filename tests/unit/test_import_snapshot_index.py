"""FEAT-36a SnapshotIndex tests.

Five things this file pins:

1. `from_snapshot` parses every record JSONL under the snapshot
   directory and indexes by `(content_type, NK)`.
2. List-shaped NKs from `json.loads` normalise to tuples on
   lookup so callers do not need to convert explicitly.
3. Audit-log files (`flags.jsonl`, `progress.jsonl`,
   `_deferred.jsonl`, `audit.jsonl`) are skipped.
4. Unknown JSONL paths are skipped silently (e.g. a content
   type we do not have in CONTENT_TYPE_FILES).
5. Malformed JSON lines do not crash the loader.
"""

from __future__ import annotations

import json
from pathlib import Path

from nbsnap.import_.snapshot_index import SnapshotIndex


def _write(path: Path, row: dict) -> None:
    """Append a single JSONL row to `path`, creating parents."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_index_finds_simple_slug_record(tmp_path: Path) -> None:
    """A site row indexed by its slug NK lookup-able by tuple."""

    _write(
        tmp_path / "dcim/sites.jsonl",
        {"natural_key": ["hall-a"], "body": {"name": "Hall-A", "slug": "a"}},
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)

    assert idx.has("dcim.site", ("hall-a",))
    body = idx.lookup("dcim.site", ("hall-a",))
    assert body == {"name": "Hall-A", "slug": "a"}


def test_index_finds_composite_nk_record(tmp_path: Path) -> None:
    """A device row indexed by composite (site, name) NK."""

    _write(
        tmp_path / "dcim/devices.jsonl",
        {
            "natural_key": [["hall-d"], "d39a"],
            "body": {"name": "d39a", "site": ["hall-d"]},
        },
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)
    body = idx.lookup("dcim.device", (("hall-d",), "d39a"))
    assert body == {"name": "d39a", "site": ["hall-d"]}


def test_lookup_accepts_list_shaped_nk(tmp_path: Path) -> None:
    """Callers that pass the JSON-deserialised list shape still
    hit the index because we normalise to tuple on lookup."""

    _write(
        tmp_path / "dcim/sites.jsonl",
        {"natural_key": ["hall-b"], "body": {"name": "Hall-B"}},
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert idx.lookup("dcim.site", ["hall-b"]) is not None
    assert idx.lookup("dcim.site", ["hall-b"]) == {"name": "Hall-B"}


# ---------------------------------------------------------------------------
# Misses
# ---------------------------------------------------------------------------


def test_missing_nk_returns_none(tmp_path: Path) -> None:
    """A query for an NK not present returns None and `has`
    returns False, no exception."""

    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert idx.lookup("dcim.site", ("ghost",)) is None
    assert idx.has("dcim.site", ("ghost",)) is False


def test_missing_content_type_returns_none(tmp_path: Path) -> None:
    """An NK in the right shape but for a content type the
    snapshot does not carry returns None."""

    _write(
        tmp_path / "dcim/sites.jsonl",
        {"natural_key": ["hall-a"], "body": {"name": "Hall-A"}},
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert idx.lookup("ipam.vlan", ("hall-a",)) is None


# ---------------------------------------------------------------------------
# Skipped files
# ---------------------------------------------------------------------------


def test_audit_files_are_skipped(tmp_path: Path) -> None:
    """`flags.jsonl` looks like a record stream but is not;
    confirm the loader skips it."""

    (tmp_path / "flags.jsonl").write_text(
        json.dumps({"content_type": "x", "field": "y", "reason": "z"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "progress.jsonl").write_text(
        json.dumps({"step": "done"}) + "\n",
        encoding="utf-8",
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert len(idx) == 0


def test_unknown_jsonl_path_is_skipped(tmp_path: Path) -> None:
    """A JSONL under an unrecognised path (e.g. a plugin
    content type) does not enter the index."""

    _write(
        tmp_path / "plugin_x/widgets.jsonl",
        {"natural_key": ["w1"], "body": {"name": "Widget 1"}},
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert len(idx) == 0


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_malformed_line_does_not_crash_loader(tmp_path: Path) -> None:
    """A bad JSON line is silently skipped; good lines still load."""

    path = tmp_path / "dcim/sites.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"natural_key": ["ok"], "body": {"name": "Good"}}) + "\n"
        + "this is not valid json\n"
        + json.dumps({"natural_key": ["also-ok"], "body": {"name": "Also"}}) + "\n",
        encoding="utf-8",
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert idx.has("dcim.site", ("ok",))
    assert idx.has("dcim.site", ("also-ok",))
    assert len(idx) == 2


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    """Trailing newlines / blank lines do not produce phantom rows."""

    path = tmp_path / "dcim/sites.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n"
        + json.dumps({"natural_key": ["site"], "body": {}}) + "\n"
        + "\n\n",
        encoding="utf-8",
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert len(idx) == 1
