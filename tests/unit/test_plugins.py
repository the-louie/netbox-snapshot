"""Plugin API tests."""

from __future__ import annotations

from nbsnap.natkey.registry import default as default_registry
from nbsnap.plugins.api import Registrar, load_all
from nbsnap.plugins.sample_bgp import NetboxBgpExtension


def test_sample_extension_registers_bgp_session_nkspec() -> None:
    registry = default_registry()
    registrar = Registrar(nk_registry=registry, field_rewriters={})
    NetboxBgpExtension().register(registrar)
    assert registry.has("netbox_bgp.bgpsession")


def test_load_all_returns_registrar_without_third_party_plugins() -> None:
    """Without any installed entry-points, load_all just builds a Registrar."""

    registrar = load_all(default_registry())
    assert isinstance(registrar, Registrar)
