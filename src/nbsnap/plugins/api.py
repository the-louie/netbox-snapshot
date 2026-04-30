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

from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Protocol

from nbsnap.natkey.model import NKRegistry, NKSpec


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


def load_all(registry: NKRegistry) -> Registrar:
    """Build a `Registrar`, run every discovered extension's `register`."""

    registrar = Registrar(nk_registry=registry, field_rewriters={})
    for ext in discover():
        try:
            ext.register(registrar)
        except Exception:  # noqa: BLE001 - skip broken plugins
            continue
    return registrar
