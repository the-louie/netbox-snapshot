"""The :class:`ContentType` value object (ARCH-05a).

The audit's complaint:

* Bare strings (``"dcim.device"``) flow through the codebase.
* Typos (``"dcim.devic"``) silently flow into helpers like
  ``CONTENT_TYPE_FILES.get(ct, fallback)`` (already hardened in
  ARCH-08a) or ``CONTENT_TYPE_ENDPOINTS.get(ct, ...)`` (covered by
  ARCH-05b/e).
* Two helpers re-implement the ``"dcim.device"`` → ``"dcim/devices/"``
  mapping.

The fix is a tiny immutable value object that:

* Parses ``app.model`` strings exactly once with
  :meth:`ContentType.from_str`, rejecting empty / multi-dot / unknown
  forms with :class:`InvalidContentTypeError`.
* Renders back to a string with :meth:`as_str`.
* Resolves the NetBox API endpoint with :meth:`endpoint`, sourcing
  the canonical map from a single ``_ENDPOINTS`` dict (ARCH-05b moves
  the existing ``CONTENT_TYPE_ENDPOINTS`` into this dict and re-exports
  for one ticket; ARCH-05e drops the shim).

The value object is intentionally minimal: no caching, no methods
beyond the three above. Bigger semantics (subtype hierarchy,
plugin-owned content types) can land later by extending the dict
or adding methods, but the type's *shape* should stay this small.
"""

from __future__ import annotations

from dataclasses import dataclass


class InvalidContentTypeError(ValueError):
    """Raised when a string is not a valid ``app.model`` content type.

    Carries the bad input on ``raw`` so the caller can render an
    operator-friendly error without re-parsing the message.
    """

    def __init__(self, raw: str, reason: str) -> None:
        self.raw = raw
        super().__init__(f"invalid content type {raw!r}: {reason}")


# Canonical content type -> REST endpoint mapping. ARCH-05b moves this
# from ``natkey/verify.py``; for the migration window
# ``natkey.verify.CONTENT_TYPE_ENDPOINTS`` is re-exported. ARCH-05e
# drops the shim.
_ENDPOINTS: dict[str, str] = {
    "dcim.site": "dcim/sites/",
    "dcim.location": "dcim/locations/",
    "dcim.rack": "dcim/racks/",
    "dcim.devicerole": "dcim/device-roles/",
    "dcim.devicetype": "dcim/device-types/",
    "dcim.manufacturer": "dcim/manufacturers/",
    "dcim.platform": "dcim/platforms/",
    "dcim.device": "dcim/devices/",
    "dcim.interface": "dcim/interfaces/",
    "dcim.frontport": "dcim/front-ports/",
    "dcim.rearport": "dcim/rear-ports/",
    "dcim.cable": "dcim/cables/",
    "ipam.role": "ipam/roles/",
    "ipam.vlan": "ipam/vlans/",
    "ipam.prefix": "ipam/prefixes/",
    "ipam.iprange": "ipam/ip-ranges/",
    "ipam.ipaddress": "ipam/ip-addresses/",
    "extras.tag": "extras/tags/",
    "extras.customfield": "extras/custom-fields/",
    "extras.customfieldchoiceset": "extras/custom-field-choice-sets/",
}


@dataclass(frozen=True)
class ContentType:
    """``app.model`` pair, hashable and immutable.

    Built via :meth:`from_str` so the validation runs exactly once
    per parse. Equality and hashing are dataclass-default
    (component-wise), so two ``ContentType`` instances with the
    same app+model compare equal and hash the same; this is what
    makes the value object cheap to use as a dict key.

    Direct construction (``ContentType(app="dcim", model="cable")``)
    bypasses the unknown-content-type check. The escape hatch exists
    for internal planner data (polymorphic targets that point at
    content types outside the renderer-minimum scope, see
    ARCH-05c). User-supplied strings should always go through
    :meth:`from_str` so the typo path is closed.
    """

    app: str
    model: str

    @classmethod
    def from_str(cls, raw: str) -> ContentType:
        """Parse a string into a :class:`ContentType`.

        Validation rules:

        * Must contain exactly one ``.``.
        * App and model parts must both be non-empty.
        * The resulting ``app.model`` must appear in ``_ENDPOINTS``;
          unknown content types are how typos get caught.

        Plugins that introduce their own content types can extend
        ``_ENDPOINTS`` at runtime via the registrar API; until that
        registration runs, ``from_str`` refuses the unknown name.
        """

        if not isinstance(raw, str):
            raise InvalidContentTypeError(str(raw), "not a string")
        if "." not in raw:
            raise InvalidContentTypeError(raw, "missing '.' separator")
        if raw.count(".") != 1:
            raise InvalidContentTypeError(raw, "expected exactly one '.'")
        app, model = raw.split(".", 1)
        if not app or not model:
            raise InvalidContentTypeError(raw, "app or model is empty")
        if raw not in _ENDPOINTS:
            raise InvalidContentTypeError(
                raw,
                f"unknown content type; expected one of {sorted(_ENDPOINTS)}",
            )
        return cls(app=app, model=model)

    def as_str(self) -> str:
        """Render back to ``app.model``."""

        return f"{self.app}.{self.model}"

    def endpoint(self) -> str:
        """Return the NetBox REST endpoint, e.g. ``"dcim/devices/"``."""

        # The lookup cannot fail: ``from_str`` already refuses unknown
        # values, so we should never reach this with a missing key.
        # Use a direct subscript so a future code path that bypasses
        # ``from_str`` fails loudly.
        return _ENDPOINTS[self.as_str()]


__all__ = ["ContentType", "InvalidContentTypeError"]
