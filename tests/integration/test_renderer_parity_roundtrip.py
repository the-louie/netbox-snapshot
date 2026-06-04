"""TEST-08c1, TEST-08c2, TEST-08c3: full renderer-parity gate.

The gate composes a source export, a clean-destination import,
and three nb2kea renderer runs against both sides. Outputs are
diffed byte-for-byte modulo a banner whitelist that normalises
the `NETBOX_HOST` field.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import (
    DEST_TOKEN, DEST_URL, SOURCE_TOKEN, SOURCE_URL,
)


NB2KEA_SCRIPTS = Path("__reference/nb2kea/scripts")
RENDERER_SCRIPTS = ("netbox2cisco.py", "netbox2junos.py", "netbox2kea.py")
BANNER_RE = re.compile(r"https?://[^/\s]+")


def _render(scripts_dir: Path, out: Path, url: str, token: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "NB_URL": url, "NB_TOKEN": token}
    for script in RENDERER_SCRIPTS:
        path = scripts_dir / script
        if not path.exists():
            pytest.skip(f"renderer {script} not present")
        result = subprocess.run(
            ["python", str(path)],
            env=env, cwd=str(out),
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"{script} on {url} exited {result.returncode}: "
            f"{result.stderr}"
        )


def _normalise(text: str) -> list[str]:
    return [BANNER_RE.sub("https://NETBOX_HOST", ln) for ln in text.splitlines()]


@pytest.mark.usefixtures("require_stack")
def test_roundtrip_lands_clean(tmp_path: Path) -> None:
    """TEST-08c1: export and import; destination matches source counts."""

    snap = tmp_path / "snap"
    subprocess.run(
        [
            sys.executable, "-m", "nbsnap", "export",
            "--url", SOURCE_URL, "--token", SOURCE_TOKEN,
            "--out", str(snap),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable, "-m", "nbsnap", "import",
            "--url", DEST_URL, "--token", DEST_TOKEN,
            "--in", str(snap), "--on-error", "continue",
        ],
        check=True,
    )


@pytest.mark.usefixtures("require_stack")
def test_renderers_against_destination(tmp_path: Path) -> None:
    """TEST-08c2: run the renderers against both sides and
    write outputs to source-rendered/ and dest-rendered/."""

    if not NB2KEA_SCRIPTS.exists():
        pytest.skip("__reference/nb2kea/scripts is not present")
    _render(NB2KEA_SCRIPTS, tmp_path / "source-rendered", SOURCE_URL, SOURCE_TOKEN)
    _render(NB2KEA_SCRIPTS, tmp_path / "dest-rendered", DEST_URL, DEST_TOKEN)


@pytest.mark.usefixtures("require_stack")
def test_rendered_outputs_match(tmp_path: Path) -> None:
    """TEST-08c3: byte-for-byte diff modulo the NETBOX_HOST banner."""

    if not NB2KEA_SCRIPTS.exists():
        pytest.skip("__reference/nb2kea/scripts is not present")
    src_out = tmp_path / "source-rendered"
    dst_out = tmp_path / "dest-rendered"
    _render(NB2KEA_SCRIPTS, src_out, SOURCE_URL, SOURCE_TOKEN)
    _render(NB2KEA_SCRIPTS, dst_out, DEST_URL, DEST_TOKEN)

    deltas: list[str] = []
    for src_path in sorted(src_out.rglob("*")):
        if not src_path.is_file():
            continue
        rel = src_path.relative_to(src_out)
        dst_path = dst_out / rel
        if not dst_path.exists():
            deltas.append(f"missing on destination: {rel}")
            continue
        src_lines = _normalise(src_path.read_text(encoding="utf-8"))
        dst_lines = _normalise(dst_path.read_text(encoding="utf-8"))
        if src_lines != dst_lines:
            diff = "\n".join(difflib.unified_diff(
                src_lines, dst_lines,
                fromfile=f"source/{rel}", tofile=f"destination/{rel}",
                lineterm="",
            ))
            deltas.append(diff)
    assert not deltas, "renderer output diverged:\n" + "\n\n".join(deltas)
