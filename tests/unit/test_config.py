"""Tests for the `.env` auto-loader in `nbsnap.config`.

The loader is intentionally tiny but it ships in every CLI run, so
the three behaviour rules (load, do not clobber, walk up) each get
their own focused test below.
"""

from __future__ import annotations

import os
from pathlib import Path

from nbsnap.config import load_dotenv


def test_load_dotenv_reads_simple_assignments(tmp_path: Path, monkeypatch) -> None:
    """A `.env` in the search root populates `os.environ`."""

    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\n# a comment\n\nBAZ=qux\n")

    # Clear any inherited values so the assertion is unambiguous.
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    monkeypatch.chdir(tmp_path)

    loaded = load_dotenv()
    assert loaded == env_file
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "qux"


def test_load_dotenv_does_not_clobber_existing_env(tmp_path: Path, monkeypatch) -> None:
    """Shell-set variables outrank `.env`, the nb2kea contract."""

    env_file = tmp_path / ".env"
    env_file.write_text("FOO=from_dot_env\n")

    monkeypatch.setenv("FOO", "from_shell")
    monkeypatch.chdir(tmp_path)

    load_dotenv()
    assert os.environ["FOO"] == "from_shell"


def test_load_dotenv_walks_up_to_parent(tmp_path: Path, monkeypatch) -> None:
    """A `.env` in the parent directory is found from a subdir."""

    (tmp_path / ".env").write_text("WALKED_UP=yes\n")
    subdir = tmp_path / "deep" / "nested"
    subdir.mkdir(parents=True)

    monkeypatch.delenv("WALKED_UP", raising=False)
    monkeypatch.chdir(subdir)

    loaded = load_dotenv()
    assert loaded == tmp_path / ".env"
    assert os.environ["WALKED_UP"] == "yes"


def test_load_dotenv_returns_none_when_no_env_file(tmp_path: Path, monkeypatch) -> None:
    """No `.env` anywhere on the upward path returns `None` cleanly."""

    isolated = tmp_path / "no_env_here"
    isolated.mkdir()
    monkeypatch.chdir(isolated)

    # Pass an explicit `start` so the loader does not climb out of
    # the temp tree into the repo's actual `.env`.
    assert load_dotenv(isolated) is None
