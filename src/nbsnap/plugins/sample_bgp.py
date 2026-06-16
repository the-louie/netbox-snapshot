"""Reference example: how to write an nbsnap plugin.

This module is the canonical worked example for a third-party
plugin. A real ``nbsnap-netbox-bgp`` extension would live in its
own repository and declare an ``nbsnap.plugin`` entry-point in its
``pyproject.toml``; this in-tree copy lets readers see the
registration shape without leaving the codebase.

Anatomy of a plugin
-------------------
Three pieces are required:

1. A class with ``name`` and ``version`` string attributes plus
   a ``register(registrar)`` method. Together they satisfy the
   :class:`nbsnap.plugins.api.PluginExtension` protocol.
2. The ``register`` method calls **only** the public
   :class:`Registrar` surface, ``add_nkspec`` and
   ``add_field_rewriter``. Plugins must not poke
   :class:`NKRegistry` directly because the registry's internals
   are not part of the public API.
3. A module-level ``plugin`` object that the directory loader
   imports as the entry point (ARCH-04a). Entry-point plugins
   wire the same object via the package's ``[project.entry-points]``
   section instead.

Layered loading
---------------
A directory plugin and an entry-point plugin can both register an
NKSpec for the same content type. The order is "entry-points
first, directory second" (see
:func:`nbsnap.plugins.api.load_all`), which lets an operator
override an installed plugin by dropping a file in
``--plugins-dir``.
"""

from __future__ import annotations

from nbsnap.natkey.model import NKField, NKSpec, Strategy
from nbsnap.plugins.api import Registrar


class NetboxBgpExtension:
    """Wires NKSpec entries for the netbox-bgp plugin's content types.

    The two content types covered here are:

    * ``netbox_bgp.bgpsession``: a composite NK of ``(device,
      local_address, remote_address)``, because a single device
      can hold many sessions and the IP pair is what makes one
      session distinct across reboots.
    * ``netbox_bgp.bgppeergroup``: a composite NK of ``(device,
      name)``, because peer groups are scoped to their device and
      the operator-chosen name is the stable identifier.
    """

    name = "netbox-bgp"
    version = "0.0.1-sketch"

    def register(self, registrar: Registrar) -> None:
        """Tell the registrar about every NKSpec this plugin owns."""

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


# Module-level instance the directory loader (ARCH-04a) and the
# entry-point loader both pick up. Keep the variable name `plugin`
# unchanged, the loader hard-codes this name.
plugin = NetboxBgpExtension()
