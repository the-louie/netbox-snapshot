"""Snapshot tree diff (FEAT-26a/b).

Compares two snapshot directories file by file. The expected use
case is "did the round-trip change anything?" so the diff routine
exits 0 when identical, 1 when material differences land, and
prints a structured per-file summary.

A configurable exclusion list lets the operator silence noise
from install-local fields (the source NetBox's hostname,
timestamps, etc.) that legitimately differ between snapshots.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default exclusions for round-trip diffs. The operator can add
# more via --ignore.
DEFAULT_EXCLUSIONS: frozenset[str] = frozenset(
    {
        "created",
        "last_updated",
        "id",  # destination id always differs
        "url",  # URL embeds destination hostname
    }
)


@dataclass
class FileDiff:
    """Per-file diff outcome."""

    path: str
    rows_only_left: list[dict[str, Any]] = field(default_factory=list)
    rows_only_right: list[dict[str, Any]] = field(default_factory=list)
    rows_changed: list[tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=list)


@dataclass
class TreeDiff:
    """Aggregate result of a tree-vs-tree diff."""

    file_diffs: list[FileDiff] = field(default_factory=list)
    missing_on_right: list[str] = field(default_factory=list)
    missing_on_left: list[str] = field(default_factory=list)

    def is_clean(self) -> bool:
        if self.missing_on_left or self.missing_on_right:
            return False
        for fd in self.file_diffs:
            if fd.rows_only_left or fd.rows_only_right or fd.rows_changed:
                return False
        return True


def diff_trees(left: Path, right: Path, ignore: frozenset[str]) -> TreeDiff:
    """Walk both snapshot trees and produce a `TreeDiff`."""

    result = TreeDiff()
    left_files = {p.relative_to(left).as_posix() for p in left.rglob("*.jsonl")}
    right_files = {p.relative_to(right).as_posix() for p in right.rglob("*.jsonl")}

    result.missing_on_right = sorted(left_files - right_files)
    result.missing_on_left = sorted(right_files - left_files)

    for rel in sorted(left_files & right_files):
        result.file_diffs.append(
            _diff_one_file(left / rel, right / rel, rel, ignore)
        )
    return result


def _diff_one_file(
    left: Path, right: Path, rel: str, ignore: frozenset[str]
) -> FileDiff:
    """Compare the two jsonl files row by row by natural key."""

    left_rows = {_nk_key(row): row for row in _iter_jsonl(left)}
    right_rows = {_nk_key(row): row for row in _iter_jsonl(right)}

    fd = FileDiff(path=rel)
    for nk in sorted(left_rows.keys() - right_rows.keys()):
        fd.rows_only_left.append(left_rows[nk])
    for nk in sorted(right_rows.keys() - left_rows.keys()):
        fd.rows_only_right.append(right_rows[nk])
    for nk in sorted(left_rows.keys() & right_rows.keys()):
        l_body = _strip_ignored(left_rows[nk].get("body") or {}, ignore)
        r_body = _strip_ignored(right_rows[nk].get("body") or {}, ignore)
        if l_body != r_body:
            fd.rows_changed.append((left_rows[nk], right_rows[nk]))
    return fd


def _nk_key(row: dict[str, Any]) -> str:
    return json.dumps(row.get("natural_key"), sort_keys=True, default=str)


def _strip_ignored(body: dict[str, Any], ignore: frozenset[str]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in ignore}


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def add_diff_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("left", type=Path, help="first snapshot directory")
    parser.add_argument("right", type=Path, help="second snapshot directory")
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="extra field name to ignore (repeatable)",
    )


def run_diff(args: argparse.Namespace) -> int:
    ignore = DEFAULT_EXCLUSIONS | frozenset(args.ignore or [])
    result = diff_trees(args.left, args.right, ignore)

    if result.is_clean():
        sys.stderr.write("# snapshots are identical (ignoring known noise)\n")
        return 0

    sys.stderr.write("# differences:\n")
    if result.missing_on_left:
        sys.stderr.write(f"  missing on left: {result.missing_on_left}\n")
    if result.missing_on_right:
        sys.stderr.write(f"  missing on right: {result.missing_on_right}\n")
    for fd in result.file_diffs:
        if fd.rows_only_left or fd.rows_only_right or fd.rows_changed:
            sys.stderr.write(
                f"  {fd.path}: only_left={len(fd.rows_only_left)} "
                f"only_right={len(fd.rows_only_right)} "
                f"changed={len(fd.rows_changed)}\n"
            )
    return 1
