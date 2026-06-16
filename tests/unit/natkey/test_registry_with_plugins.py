"""ARCH-04a: ``with_plugins`` builds the default registry plus any plugins.

Three cases under test:

* ``with_plugins(None)`` returns a registry equivalent to ``default()``
  (no env var set).
* ``with_plugins(directory)`` with a directory containing one valid
  plugin returns a registry with the extra NKSpec registered.
* ``with_plugins(directory)`` with a directory containing a
  malformed plugin raises :class:`PluginLoadError`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nbsnap.natkey.registry import default, with_plugins
from nbsnap.plugins.api import PluginLoadError


VALID_PLUGIN = """
from nbsnap.natkey.model import NKField, NKSpec, Strategy
from nbsnap.plugins.api import Registrar


class MyPlugin:
    name = "my-plugin"
    version = "0.0.1"

    def register(self, registrar: Registrar) -> None:
        registrar.add_nkspec(
            NKSpec(
                content_type="my_app.widget",
                strategy=Strategy.SLUG,
                fields=(NKField("slug"),),
            )
        )


plugin = MyPlugin()
"""


BROKEN_PLUGIN = """
this is not valid python (syntax error
"""


def test_with_plugins_none_matches_default() -> None:
    """Calling ``with_plugins(None)`` is the same as ``default()`` when
    no plugins env var is set, modulo any entry-point plugins which
    are installation-specific.
    """

    baseline = default()
    extended = with_plugins(None)
    # Without entry-point plugins installed in the test env the two
    # registries should have the same content-type set.
    assert set(spec.content_type for spec in extended) == set(
        spec.content_type for spec in baseline
    )


def test_with_plugins_directory_registers_new_nkspec(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "widget_plugin.py").write_text(VALID_PLUGIN, encoding="utf-8")

    registry = with_plugins(plugins)
    assert registry.has("my_app.widget")


def test_with_plugins_directory_raises_on_broken_plugin(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "broken.py").write_text(BROKEN_PLUGIN, encoding="utf-8")

    with pytest.raises(PluginLoadError) as exc:
        with_plugins(plugins)
    assert "broken.py" in str(exc.value)


def test_with_plugins_env_var_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If no directory is passed, the env var picks up the slack."""

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "widget_plugin.py").write_text(VALID_PLUGIN, encoding="utf-8")
    monkeypatch.setenv("NBSNAP_PLUGINS_DIR", str(plugins))

    registry = with_plugins(None)
    assert registry.has("my_app.widget")
