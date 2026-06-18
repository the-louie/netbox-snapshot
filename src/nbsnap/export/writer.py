"""JSONL writer with atomic file replace and stable sort (FEAT-14a/b).

The writer takes a stream of `ExtractedRow` from the extractor
and lays out the snapshot directory as documented in RES-03.

* Each content type lands in `<app>/<plural>.jsonl`.
* Rows are sorted by natural-key tuple so re-running the export
  against the same source produces byte-identical files.
* Files are written via a `.tmp` and atomic rename so a crash
  mid-write does not leave a half-finished file the importer
  could later read.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from nbsnap.export.extractor import ExtractedRow
from nbsnap.snapshot.layout import relative_path


def write_content_type(snapshot_dir: Path, content_type: str, rows: Iterable[ExtractedRow]) -> int:
    """Sort `rows` by NK and write to `<snapshot_dir>/<file>`.

    Returns the number of rows written so the caller can update
    the manifest count.

    Sort key is the natural-key tuple converted to a sortable list
    (NKs nest tuples and primitives, json.dumps gives us a stable
    string ordering that mirrors lexical comparison).
    """
    target = snapshot_dir / relative_path(content_type)
    target.parent.mkdir(parents=True, exist_ok=True)

    sorted_rows = sorted(
        rows,
        key=lambda row: json.dumps(row.natural_key, sort_keys=True, default=str),
    )

    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        for row in sorted_rows:
            line = json.dumps(
                {"natural_key": list(_jsonable(row.natural_key)), "body": row.body},
                sort_keys=True,
                default=str,
            )
            fp.write(line + "\n")
    # Atomic rename. `os.replace` is atomic on POSIX, so the file
    # only appears under its real name once write is complete.
    os.replace(tmp, target)
    return len(sorted_rows)


def _jsonable(value: Any) -> Any:
    """Convert a tuple-tree NK into something json.dumps can sort.

    `json.dumps` does not encode tuples as a distinct type, the
    encoder writes them as JSON arrays. We convert to lists so
    `sort_keys=True` traverses cleanly.
    """
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value
