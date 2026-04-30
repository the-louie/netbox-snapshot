"""Reference sketch: netbox-bgp extension (FEAT-32).

This is *not* shipped as a real plugin in v1; it lives in-tree as
a worked example so a reader can see the registration shape. A
real netbox-bgp extension would live in `nbsnap-netbox-bgp` and
declare an entry-point in its own pyproject.toml.
"""

from __future__ import annotations

from nbsnap.natkey.model import NKField, NKSpec, Strategy
from nbsnap.plugins.api import Registrar


class NetboxBgpExtension:
    """Wires NKSpec entries for `netbox_bgp.bgpsession` and friends."""

    name = "netbox-bgp"
    version = "0.0.1-sketch"

    def register(self, registrar: Registrar) -> None:
        registrar.add_nkspec(
            NKSpec(
                content_type="netbox_bgp.bgpsession",
                strategy=Strategy.COMPOSITE,
                fields=(
                    NKField("device", "dcim.device"),
                    NKField("local_address"),
                    NKField("remote_address"),
                ),
            )
        )
        registrar.add_nkspec(
            NKSpec(
                content_type="netbox_bgp.bgppeergroup",
                strategy=Strategy.COMPOSITE,
                fields=(NKField("device", "dcim.device"), NKField("name")),
            )
        )


# Module-level instance the entry-point would load.
plugin = NetboxBgpExtension()
