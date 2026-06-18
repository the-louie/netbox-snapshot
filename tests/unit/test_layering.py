"""ARCH-01g: enforce the snapshot/export/import_ layering rule.

The layering rule, completed by ARCH-01f, is:

* :mod:`nbsnap.snapshot` is the shared data contract. It must not
  import from :mod:`nbsnap.export` or :mod:`nbsnap.import_`,
  otherwise the contract would silently take on a side of the
  pipeline as a dependency.
* :mod:`nbsnap.export` and :mod:`nbsnap.import_` are peers. Neither
  may import from the other. Both consume the shared
  :mod:`nbsnap.snapshot` contract.

This test walks each package with :mod:`ast` (not regex, so
re-aliased imports cannot slip through), collects every offending
import line, and reports them all at once so the operator can fix
the layering in a single pass.

It replaced the narrower ARCH-01e regression file
(``tests/unit/test_import_no_export_imports.py``) once ARCH-01g
covered the same direction along with the symmetric one. There is
no second guard; this is THE layering invariant.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "nbsnap"
SNAPSHOT = ROOT / "snapshot"
EXPORT = ROOT / "export"
IMPORT_ = ROOT / "import_"


def _python_files(root: Path) -> list[Path]:
    """Return every ``*.py`` file under ``root`` (skip caches)."""

    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _imported_modules(py_path: Path) -> list[tuple[int, str]]:
    """Yield ``(lineno, module_name)`` for every absolute import in ``py_path``.

    We resolve ``from nbsnap.x.y import Z`` into ``"nbsnap.x.y"``,
    and ``import nbsnap.x.y`` (or ``import nbsnap.x.y as alias``)
    into ``"nbsnap.x.y"``. Relative ``from . import`` is ignored
    because intra-package imports are allowed.
    """

    source = py_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_path))

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                out.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
    return out


def _offending_imports(
    package_root: Path, forbidden_prefixes: tuple[str, ...]
) -> list[tuple[Path, int, str]]:
    """Walk ``package_root`` and return every import that starts with one of ``forbidden_prefixes``."""

    findings: list[tuple[Path, int, str]] = []
    for py_path in _python_files(package_root):
        for lineno, module in _imported_modules(py_path):
            for prefix in forbidden_prefixes:
                if module == prefix or module.startswith(prefix + "."):
                    findings.append((py_path, lineno, module))
                    break
    return findings


def test_snapshot_does_not_import_from_pipeline_sides() -> None:
    """nbsnap.snapshot must not depend on export/ or import_/."""

    findings = _offending_imports(SNAPSHOT, forbidden_prefixes=("nbsnap.export", "nbsnap.import_"))
    assert not findings, (
        "nbsnap.snapshot is a shared contract; it must not import from a "
        f"pipeline side. Offenders: {findings}"
    )


def test_export_does_not_import_from_import_() -> None:
    """nbsnap.export and nbsnap.import_ are peers; no cross-side imports."""

    findings = _offending_imports(EXPORT, forbidden_prefixes=("nbsnap.import_",))
    assert not findings, f"nbsnap.export must not import from nbsnap.import_. Offenders: {findings}"


def test_import_does_not_import_from_export() -> None:
    """The symmetric direction of the peer rule, covered separately so a
    failure reports against the right side cleanly."""

    findings = _offending_imports(IMPORT_, forbidden_prefixes=("nbsnap.export",))
    assert not findings, f"nbsnap.import_ must not import from nbsnap.export. Offenders: {findings}"
