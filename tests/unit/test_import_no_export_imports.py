"""ARCH-01e: no ``from nbsnap.export`` import survives under ``import_/``.

The import package and the export package are peers from ARCH-01
onward; both depend on :mod:`nbsnap.snapshot` for the on-disk
contract. This test walks every Python file under ``src/nbsnap/import_/``
and refuses any ``from nbsnap.export`` reference. ARCH-01g extends
this guard to cover the symmetric direction (export must not import
from import_).

We use :mod:`ast` rather than a regex so re-aliased imports
(``from nbsnap.export.manifest import Manifest as M``) still count.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

IMPORT_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "nbsnap" / "import_"


def _python_files(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


@pytest.mark.parametrize(
    "py_path",
    _python_files(IMPORT_PACKAGE),
    ids=lambda p: str(p.relative_to(IMPORT_PACKAGE)),
)
def test_import_module_does_not_import_from_export(py_path: Path) -> None:
    source = py_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_path))

    offending: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "nbsnap.export" or module.startswith("nbsnap.export."):
                offending.append((node.lineno, module))
        # Cover ``import nbsnap.export`` and ``import nbsnap.export.foo``
        # too, since the ImportFrom check alone would miss them.
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "nbsnap.export" or name.startswith("nbsnap.export."):
                    offending.append((node.lineno, name))

    assert not offending, (
        f"{py_path} still imports from nbsnap.export: {offending}; "
        "switch to nbsnap.snapshot per ARCH-01e"
    )
