"""Default NKSpec registry for the renderer-minimum scope.

Lands the entries from FEAT-08b1 (DCIM Site → Cable), FEAT-08b2
(DCIM port + inventory + all IPAM), and FEAT-08b3 (Tenancy +
decorating Extras). Tenancy is now out of scope per the
"NETWORK MODEL ONLY" banner in CLAUDE.md, the registry still ships
the entries so a plugin extension can opt back in but `default()`
filters them out for the v1 base.

ARCH-04a: :func:`with_plugins` is the factory that the CLIs reach
for, it builds the default registry then asks the plugin loader to
extend it from either an explicit directory or the
``NBSNAP_PLUGINS_DIR`` env-var fallback.
"""

from __future__ import annotations

import os
from pathlib import Path

from nbsnap.natkey.model import NKField, NKRegistry, NKSpec, Strategy


def with_plugins(directory: Path | None = None) -> NKRegistry:
    """Return :func:`default` plus any plugin-registered NKSpecs.

    Resolution order for ``directory``:

    1. The explicit argument, when not None.
    2. The ``NBSNAP_PLUGINS_DIR`` environment variable.
    3. None, meaning "no directory plugins". Entry-point plugins
       discovered via ``importlib.metadata`` are still loaded so an
       installed extension keeps working.

    Raises :class:`nbsnap.plugins.api.PluginLoadError` if a
    directory plugin fails to import; entry-point plugins still
    swallow exceptions per the existing :func:`discover` contract,
    because they are not operator-controlled.
    """

    # Import locally to avoid a circular import on module load.
    from nbsnap.plugins.api import load_all

    registry = default()
    resolved: Path | None
    if directory is not None:
        resolved = directory
    else:
        env = os.environ.get("NBSNAP_PLUGINS_DIR")
        resolved = Path(env) if env else None
    load_all(registry, directory=resolved)
    return registry


def default() -> NKRegistry:
    """Return a populated registry covering the renderer-minimum set."""

    r = NKRegistry()

    # ------------------------------------------------------------------
    # Organisational hierarchy above Site, registered with slug NKs
    # so an FK like dcim.site.region resolves cleanly. Region and
    # SiteGroup are themselves out of the network-only scope, but
    # the sites we ARE exporting carry FKs into them, and dropping
    # the FKs silently would lose data. Tenants stay unregistered;
    # the network-only banner forbids tenant export so any tenant
    # FK on a site is left as a hard drop by the rewriter.
    # ------------------------------------------------------------------
    r.register(NKSpec("dcim.region", Strategy.SLUG, (NKField("slug"),)))
    r.register(NKSpec("dcim.sitegroup", Strategy.SLUG, (NKField("slug"),)))

    # ------------------------------------------------------------------
    # FEAT-08b1, DCIM (Site through Cable)
    # ------------------------------------------------------------------
    r.register(NKSpec("dcim.site", Strategy.SLUG, (NKField("slug"),)))
    r.register(
        NKSpec(
            "dcim.location",
            Strategy.COMPOSITE,
            (NKField("site", "dcim.site"), NKField("slug")),
        )
    )
    r.register(
        NKSpec(
            "dcim.rack",
            Strategy.COMPOSITE,
            (NKField("site", "dcim.site"), NKField("name")),
        )
    )
    r.register(NKSpec("dcim.manufacturer", Strategy.SLUG, (NKField("slug"),)))
    r.register(
        NKSpec(
            "dcim.devicetype",
            Strategy.COMPOSITE,
            (NKField("manufacturer", "dcim.manufacturer"), NKField("slug")),
        )
    )
    r.register(NKSpec("dcim.devicerole", Strategy.SLUG, (NKField("slug"),)))
    r.register(NKSpec("dcim.platform", Strategy.SLUG, (NKField("slug"),)))
    r.register(
        NKSpec(
            "dcim.device",
            Strategy.COMPOSITE,
            (NKField("site", "dcim.site"), NKField("name")),
        )
    )
    r.register(NKSpec("dcim.cable", Strategy.POLYMORPHIC_SET, ()))

    # ------------------------------------------------------------------
    # FEAT-08b2, DCIM ports + IPAM
    # ------------------------------------------------------------------
    r.register(
        NKSpec(
            "dcim.interface",
            Strategy.COMPOSITE,
            (NKField("device", "dcim.device"), NKField("name")),
        )
    )
    r.register(
        NKSpec(
            "dcim.frontport",
            Strategy.COMPOSITE,
            (NKField("device", "dcim.device"), NKField("name")),
        )
    )
    r.register(
        NKSpec(
            "dcim.rearport",
            Strategy.COMPOSITE,
            (NKField("device", "dcim.device"), NKField("name")),
        )
    )
    r.register(NKSpec("ipam.role", Strategy.SLUG, (NKField("slug"),)))
    r.register(
        NKSpec(
            "ipam.vlan",
            Strategy.COMPOSITE,
            (NKField("site", "dcim.site"), NKField("vid")),
        )
    )
    r.register(NKSpec("ipam.prefix", Strategy.SLUG, (NKField("prefix"),)))
    r.register(
        NKSpec(
            "ipam.iprange",
            Strategy.COMPOSITE,
            (NKField("start_address"), NKField("end_address")),
        )
    )
    r.register(
        NKSpec(
            "ipam.ipaddress",
            Strategy.COMPOSITE,
            (
                NKField("address"),
                NKField("assigned_object_type"),
                NKField("assigned_object_id"),
            ),
        )
    )

    # ------------------------------------------------------------------
    # FEAT-08b3, Extras
    # ------------------------------------------------------------------
    r.register(NKSpec("extras.tag", Strategy.SLUG, (NKField("slug"),)))
    r.register(NKSpec("extras.customfield", Strategy.SLUG, (NKField("name"),)))
    r.register(NKSpec("extras.customfieldchoiceset", Strategy.SLUG, (NKField("name"),)))

    return r
