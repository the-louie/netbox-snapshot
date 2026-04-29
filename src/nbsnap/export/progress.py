"""Per-row progress log and resume helpers (FEAT-16a/b).

`progress.jsonl` is an append-only log of `(content_type,
natural_key, status)` triples. The export driver writes a row per
processed record so a resumed export can fast-forward past
content types that have been completed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROGRESS_FILENAME = "progress.jsonl"


class ProgressLog:
    """Append-only JSONL log."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self, content_type: str, natural_key: Any, status: str
    ) -> None:
        payload = {
            "content_type": content_type,
            "natural_key": natural_key,
            "status": status,
        }
        with self._path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def resume_from(path: Path) -> set[str]:
    """Read `progress.jsonl`, return the set of completed content types.

    A content type is considered complete if its `status == "done"`
    line is present. Per-row resumption is not supported in v1, the
    granularity is content type.
    """
    target = Path(path)
    if not target.exists():
        return set()
    done: set[str] = set()
    for raw in target.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "done":
            ct = row.get("content_type")
            if isinstance(ct, str):
                done.add(ct)
    return done
