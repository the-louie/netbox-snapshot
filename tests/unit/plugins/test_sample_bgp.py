"""ARCH-04b: ``sample_bgp`` registers cleanly through the Registrar surface.

We do not check internals of :class:`NKRegistry`, the test only
asserts that loading the sample plugin via the public Registrar
results in the two NKSpecs the example documents. A reader
copying this pattern for a real plugin should see the same two
calls work.
"""

from __future__ import annotations

from nbsnap.natkey.model import NKRegistry
from nbsnap.plugins.api import Registrar
from nbsnap.plugins.sample_bgp import plugin


def test_sample_bgp_registers_bgpsession() -> None:
    registry = NKRegistry()
    registrar = Registrar(nk_registry=registry, field_rewriters={})

    plugin.register(registrar)

    assert registry.has("netbox_bgp.bgpsession")
    spec = registry.get("netbox_bgp.bgpsession")
    assert spec.content_type == "netbox_bgp.bgpsession"
    assert spec.field_names == ("device", "local_address", "remote_address")


def test_sample_bgp_registers_bgppeergroup() -> None:
    registry = NKRegistry()
    registrar = Registrar(nk_registry=registry, field_rewriters={})

    plugin.register(registrar)

    assert registry.has("netbox_bgp.bgppeergroup")
    spec = registry.get("netbox_bgp.bgppeergroup")
    assert spec.field_names == ("device", "name")


def test_sample_bgp_satisfies_protocol() -> None:
    """The class exposes ``name`` and ``version`` strings plus register()."""

    assert isinstance(plugin.name, str)
    assert isinstance(plugin.version, str)
    assert callable(plugin.register)
