"""ARCH-07e: no module outside ``nbsnap.http`` imports :mod:`requests`.

The HTTP boundary rule (ARCH-07):

* :mod:`nbsnap.http` owns the ``requests`` dependency. Inside the
  package we wrap ``requests`` types into nbsnap-domain exceptions
  (:class:`SnapshotConnectivityError`, :class:`SnapshotAuthError`,
  :class:`SnapshotTransportError`) so the rest of the codebase
  reasons about failures in nbsnap terms.

* Outside :mod:`nbsnap.http`, nothing imports ``requests`` directly,
  not even ``requests.exceptions``. ARCH-07c removed the last such
  catch in ``import_cli.py``; ARCH-07d documented the
  :class:`NetboxHTTPError` catches as domain catches and pointed
  them at the public ``nbsnap.http`` re-export.

This test walks every Python file under ``src/nbsnap`` and refuses
any ``import requests`` / ``from requests ...`` outside ``nbsnap.http``.
Uses :mod:`ast` so aliased imports cannot slip through.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "nbsnap"
HTTP_PACKAGE = ROOT / "http"


def _python_files_outside_http() -> list[Path]:
    return [
        p
        for p in ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
        and HTTP_PACKAGE not in p.parents
        and p != HTTP_PACKAGE
    ]


def test_no_requests_imports_outside_http_package() -> None:
    offenders: list[tuple[Path, int, str]] = []
    for py_path in _python_files_outside_http():
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "requests"
            ):
                offenders.append((py_path, node.lineno, f"from {node.module}"))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "requests" or alias.name.startswith("requests."):
                        offenders.append((py_path, node.lineno, f"import {alias.name}"))

    assert not offenders, (
        "nbsnap modules outside nbsnap.http must not import requests. "
        f"Offenders: {offenders}"
    )
