"""TEST-04: export from the seeded source twice and assert
the per-content-type JSONL files are byte-identical.

`manifest.exported_at` and any other timer-derived values are
the only allowed delta.
"""

from __future__ import annotations

import difflib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import SOURCE_TOKEN, SOURCE_URL


@pytest.mark.usefixtures("require_stack")
def test_export_runs_produce_identical_jsonl(tmp_path: Path) -> None:
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"

    for out in (out_a, out_b):
        subprocess.run(
            [
                sys.executable, "-m", "nbsnap", "export",
                "--url", SOURCE_URL,
                "--token", SOURCE_TOKEN,
                "--out", str(out),
            ],
            check=True,
        )

    delta: list[str] = []
    for path_a in sorted(out_a.rglob("*.jsonl")):
        rel = path_a.relative_to(out_a)
        path_b = out_b / rel
        if not path_b.exists():
            delta.append(f"missing on b: {rel}")
            continue
        text_a = path_a.read_text(encoding="utf-8").splitlines()
        text_b = path_b.read_text(encoding="utf-8").splitlines()
        if text_a != text_b:
            diff = "\n".join(
                difflib.unified_diff(
                    text_a, text_b,
                    fromfile=f"a/{rel}", tofile=f"b/{rel}",
                    lineterm="",
                )
            )
            delta.append(diff)

    # Allow only manifest.exported_at to diverge.
    manifest_a = json.loads((out_a / "manifest.json").read_text())
    manifest_b = json.loads((out_b / "manifest.json").read_text())
    manifest_a.pop("exported_at", None)
    manifest_b.pop("exported_at", None)
    assert manifest_a == manifest_b, "manifest differs beyond exported_at"

    assert not delta, "exports diverged:\n" + "\n\n".join(delta)

    # SEC-04b: the snapshot must not leak the source URL. The literal
    # URL is replaced by source_url_hash in the Manifest dataclass
    # (SEC-04a); this assertion holds that contract end to end and
    # also catches a future jsonl that accidentally carries an
    # install-local URL through.
    for path in sorted(out_a.rglob("*.json")) + sorted(out_a.rglob("*.jsonl")):
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text, (
            f"{path.relative_to(out_a)} contains an http:// literal; "
            "the snapshot must not persist install-local URLs"
        )
        assert "https://" not in text, (
            f"{path.relative_to(out_a)} contains an https:// literal; "
            "the snapshot must not persist install-local URLs"
        )
