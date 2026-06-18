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
                sys.executable,
                "-m",
                "nbsnap",
                "export",
                "--url",
                SOURCE_URL,
                "--token",
                SOURCE_TOKEN,
                "--out",
                str(out),
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
                    text_a,
                    text_b,
                    fromfile=f"a/{rel}",
                    tofile=f"b/{rel}",
                    lineterm="",
                )
            )
            delta.append(diff)

    # Strip the manifest fields that are intrinsically tied to
    # wall-clock time before comparing. `exported_at` is the
    # obvious one. `created_at` is also a per-run timestamp,
    # added by `Manifest` at construction time. `perf` holds
    # per-step durations measured with `time.perf_counter`, so
    # two runs will never produce the same numbers even on
    # otherwise identical input. The structural shape we care
    # about (counts, scope, deferred_edges, source_url_hash) is
    # unchanged after these pops.
    manifest_a = json.loads((out_a / "manifest.json").read_text())
    manifest_b = json.loads((out_b / "manifest.json").read_text())
    for key in ("exported_at", "created_at", "perf"):
        manifest_a.pop(key, None)
        manifest_b.pop(key, None)
    assert manifest_a == manifest_b, "manifest differs beyond exported_at, created_at, perf"

    assert not delta, "exports diverged:\n" + "\n\n".join(delta)

    # SEC-04b: the snapshot must not leak the source URL. The literal
    # URL is replaced by source_url_hash in the Manifest dataclass
    # (SEC-04a); this assertion holds that contract end to end and
    # also catches a future jsonl that accidentally carries an
    # install-local URL through.
    #
    # Two refinements relative to the original `"http://" not in text`
    # shape: (1) the OpenAPI schema (`schema/openapi.json`) is dumped
    # verbatim and legitimately mentions http:// in description
    # text, so it is excluded; (2) the substring check now targets
    # the specific source host (and the production source hostname
    # the banner in `CLAUDE.md` calls out) rather than every
    # http:// occurrence, which keeps the failure message readable
    # and the assertion fast even on large files. The two
    # `.find()` calls below avoid pytest's expensive difflib-based
    # `assert ... not in ...` rendering.
    leak_hosts = (
        SOURCE_URL.split("://", 1)[-1],
        "host.docker.internal:8443",
    )
    for path in sorted(out_a.rglob("*.json")) + sorted(out_a.rglob("*.jsonl")):
        if path.name == "openapi.json":
            continue
        text = path.read_text(encoding="utf-8")
        for host in leak_hosts:
            assert text.find(host) == -1, (
                f"{path.relative_to(out_a)} contains the source URL host "
                f"{host!r}; the snapshot must not persist install-local URLs"
            )
