"""Snapshot value coercion helpers (ARCH-01d).

Right now this module owns exactly one helper, :func:`collapse_enum_dict`,
which is the GET-vs-POST asymmetry shim for NetBox choice fields.
Before ARCH-01d the helper lived under
:mod:`nbsnap.export.extractor` as ``_collapse_enum_dict`` (private
by name only, both ``import_/body_preparer.py`` and
``import_/upsert.py`` reached into ``export/`` to use it). Promoting
it to the snapshot package gives every consumer a peer-level home.

Why this is a snapshot concern, not an export concern
-----------------------------------------------------
The coercion is a *contract about the on-disk shape*: the snapshot
must carry NetBox choice fields as the bare value (string, int,
bool, or None), not the ``{"value": "...", "label": "..."}``
wrapper that NetBox returns on a GET. Both the export side (when
writing) and the import side (when rewinding a legacy snapshot
that has not been re-exported yet, see BUG-01a/BUG-09) need to
enforce the same rule. The :mod:`nbsnap.snapshot` package is the
right place for any rule that applies symmetrically to both sides.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# NetBox 4.x serialises every enum field with a `{value, label}`
# wrapper on the GET response, but the matching POST/PATCH endpoint
# accepts only the bare value string. This set is the exact key
# shape we collapse; checking for equality (not subset) guards
# against accidentally collapsing a payload dict that happens to
# carry a `value` field alongside other keys.
ENUM_DICT_KEYS = frozenset({"value", "label"})


def collapse_enum_dict(value: Any) -> Any:
    """Return the ``value`` slot when ``value`` is a NetBox enum-dict.

    NetBox returns choice fields like ``status`` as
    ``{"value": "active", "label": "Active"}`` when you read them
    via GET. The same field on POST/PATCH must be the bare string
    ``"active"``. Without this collapse the import side gets

        HTTP 400 {"status": ["Value must be passed directly..."]}

    on every record that carries a choice field, which in practice
    means every Site, Device, IPAddress, Prefix, etc.

    We only collapse when the dict has EXACTLY the two keys we
    expect. Any extra key suggests a real payload dict rather than
    the enum wrapper, so we leave it alone.
    """

    if isinstance(value, Mapping) and frozenset(value.keys()) == ENUM_DICT_KEYS:
        inner = value["value"]
        # NetBox's enum values are strings in every observed case,
        # but we allow int / bool / None for safety so a future
        # choice type does not silently get mangled. The `X | Y`
        # union syntax requires Python 3.10+ and the project floor
        # is 3.11 (see pyproject.toml).
        if isinstance(inner, str | int | bool) or inner is None:
            return inner
    return value


__all__ = ["ENUM_DICT_KEYS", "collapse_enum_dict"]
