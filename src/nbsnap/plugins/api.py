"""Plugin extension protocol and discovery (FEAT-31a/b).

Third-party packages can register a `PluginExtension` via the
`nbsnap.plugin` entry-point group. At nbsnap start time we walk
the group and call `register` on each, passing a `Registrar` the
extension uses to add NKSpec entries, custom FK rewriters, and
custom resolvers.

The protocol is intentionally small. Big plugins live in their
own repos; the v1 contract here is enough for the reference
plugin (FEAT-32) to round-trip its data type.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from nbsnap.natkey.model import NKRegistry, NKSpec


class PluginLoadError(RuntimeError):
    """Raised when a plugin file cannot be imported or registered.

    ARCH-04a. Directory-based plugins are operator-controlled; a
    typo should fail the import loudly so the operator catches it
    before the run, not silently skip the file. The exception
    preserves the source path and chains the original cause via
    ``__cause__``.
    """

    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        super().__init__(f"{path}: {message}")


@dataclass
class Registrar:
    """The object handed to each plugin's `register` callback."""

    nk_registry: NKRegistry
    field_rewriters: dict[str, Callable[[Any], Any]]

    def add_nkspec(self, spec: NKSpec) -> None:
        """Register an NKSpec for a plugin-owned content type."""
        self.nk_registry.register(spec)

    def add_field_rewriter(
        self, content_type: str, field: str, rewriter: Callable[[Any], Any]
    ) -> None:
        """Add a custom rewriter for `(content_type, field)`."""
        self.field_rewriters[f"{content_type}.{field}"] = rewriter


class PluginExtension(Protocol):
    """The protocol every plugin module's top-level object must satisfy."""

    name: str
    version: str

    def register(self, registrar: Registrar) -> None:
        ...


def discover(group: str = "nbsnap.plugin") -> list[PluginExtension]:
    """Walk the entry-point group, return every loaded extension."""

    extensions: list[PluginExtension] = []
    # Python 3.11+ shape; we floor at 3.11 in pyproject.toml so the
    # legacy dict-of-entrypoints API does not need supporting.
    eps = metadata.entry_points(group=group)
    for ep in eps:
        try:
            obj = ep.load()
        except Exception:  # noqa: BLE001 - skip broken plugins
            continue
        extensions.append(obj)
    return extensions


def _load_from_directory(directory: Path, registrar: Registrar) -> None:
    """Import every ``.py`` plugin file under ``directory`` and register it.

    ARCH-04a. The contract:

    * Every ``.py`` file at the top level of ``directory`` is loaded
      as a separate module. The file's module-level ``plugin``
      object is treated as the :class:`PluginExtension`. Files
      whose name starts with ``_`` are skipped (private modules,
      ``__init__.py`` if the directory is also a Python package).
    * A file that fails to import or whose ``plugin`` does not
      satisfy the protocol raises :class:`PluginLoadError` with the
      original exception chained. This is a hard fail so the
      operator catches typos before the run; entry-point loading
      keeps the swallow-and-skip behaviour because those plugins
      are not operator-controlled.

    Each plugin file's parent directory is briefly prepended to
    ``sys.path`` so a plugin can ``from sample_bgp_helpers import X``
    without packaging ceremony.
    """

    directory = directory.resolve()
    if not directory.is_dir():
        raise PluginLoadError(directory, "plugins-dir is not a directory")

    sys_path_added = str(directory) not in sys.path
    if sys_path_added:
        sys.path.insert(0, str(directory))
    try:
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"nbsnap_plugin_{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise PluginLoadError(path, "could not build module spec")
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001
                raise PluginLoadError(path, f"import failed: {exc}") from exc

            plugin = getattr(module, "plugin", None)
            if plugin is None:
                raise PluginLoadError(
                    path,
                    "missing module-level `plugin` object (a PluginExtension)",
                )
            try:
                plugin.register(registrar)
            except Exception as exc:  # noqa: BLE001
                raise PluginLoadError(
                    path, f"plugin.register() raised: {exc}"
                ) from exc
    finally:
        if sys_path_added:
            sys.path.remove(str(directory))


def load_all(registry: NKRegistry, directory: Path | None = None) -> Registrar:
    """Build a Registrar, run every discovered extension's ``register``.

    Entry-point plugins (``nbsnap.plugin`` group) are discovered as
    before. When ``directory`` is provided, every ``.py`` file under
    it is loaded too (ARCH-04a).

    Order: entry-point plugins first, directory plugins second, so a
    directory plugin can override an entry-point spec on the same
    content type.
    """

    registrar = Registrar(nk_registry=registry, field_rewriters={})
    for ext in discover():
        try:
            ext.register(registrar)
        except Exception:  # noqa: BLE001 - skip broken entry-point plugins
            continue
    if directory is not None:
        _load_from_directory(directory, registrar)
    return registrar


__all__ = [
    "PluginExtension",
    "PluginLoadError",
    "Registrar",
    "discover",
    "load_all",
]
