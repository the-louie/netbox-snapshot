"""Default NKSpec registry for the renderer-minimum scope.

Lands the entries from FEAT-08b1 (DCIM Site → Cable), FEAT-08b2
(DCIM port + inventory + all IPAM), and FEAT-08b3 (Tenancy +
decorating Extras). Tenancy is now out of scope per the
"NETWORK MODEL ONLY" banner in CLAUDE.md, the registry still ships
the entries so a plugin extension can opt back in but `default()`
filters them out for the v1 base.
"""

from __future__ import annotations

from nbsnap.natkey.model import NKField, NKRegistry, NKSpec, Strategy


def default() -> NKRegistry:
    """Return a populated registry covering the renderer-minimum set."""

    r = NKRegistry()

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
