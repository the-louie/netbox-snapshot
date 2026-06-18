"""TEST-08b: run nb2kea renderers against the source stack
and assert each script exits 0 with the expected file count.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.integration.conftest import SOURCE_TOKEN, SOURCE_URL


NB2KEA_SCRIPTS = Path("__reference/nb2kea/scripts")
RENDERER_SCRIPTS = ("netbox2cisco.py", "netbox2junos.py", "netbox2kea.py")


def _skip_if_renderer_missing() -> None:
    if not NB2KEA_SCRIPTS.exists():
        pytest.skip("__reference/nb2kea/scripts is not present")


@pytest.mark.usefixtures("require_stack")
def test_nb2kea_renderers_run_against_source(tmp_path: Path) -> None:
    _skip_if_renderer_missing()
    env = {**os.environ, "NB_URL": SOURCE_URL, "NB_TOKEN": SOURCE_TOKEN}
    out_dir = tmp_path / "source-rendered"
    out_dir.mkdir()
    for script in RENDERER_SCRIPTS:
        path = NB2KEA_SCRIPTS / script
        if not path.exists():
            pytest.skip(f"renderer {script} not present")
        result = subprocess.run(
            ["python", str(path)],
            env=env,
            cwd=str(out_dir),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{script} exited {result.returncode}: {result.stderr}"
    rendered = list(out_dir.rglob("*"))
    assert rendered, "no renderer output produced"
