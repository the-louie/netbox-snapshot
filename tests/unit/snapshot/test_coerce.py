"""ARCH-01d: :func:`nbsnap.snapshot.coerce.collapse_enum_dict` tests.

Mirrors the existing ``tests/unit/test_export_enum_collapse.py``
coverage under the new canonical import path. The ``test_legacy_alias``
case locks the back-compat shim, so a regression that pointed the
legacy name at a different object would fail loudly.
"""

from __future__ import annotations

from nbsnap.snapshot.coerce import ENUM_DICT_KEYS, collapse_enum_dict


def test_collapse_two_key_enum_dict_returns_value() -> None:
    assert collapse_enum_dict({"value": "active", "label": "Active"}) == "active"


def test_collapse_non_string_inner_values_pass_through() -> None:
    """The int/bool/None safety branch in collapse_enum_dict.

    NetBox emits strings today, but the helper deliberately handles
    int, bool, and None too so a future choice type ('priority': 1)
    is not silently mangled. Lock the safety net.
    """

    assert collapse_enum_dict({"value": 42, "label": "forty-two"}) == 42
    assert collapse_enum_dict({"value": True, "label": "yes"}) is True
    assert collapse_enum_dict({"value": None, "label": "none"}) is None


def test_collapse_dict_with_extra_keys_is_unchanged() -> None:
    """Three-key dicts are real payloads, not enum wrappers."""

    nested_site = {"value": 1, "label": "site", "url": "/dcim/sites/1/"}
    assert collapse_enum_dict(nested_site) == nested_site


def test_collapse_non_dict_passthrough() -> None:
    assert collapse_enum_dict("active") == "active"
    assert collapse_enum_dict(42) == 42
    assert collapse_enum_dict(None) is None
    assert collapse_enum_dict([]) == []


def test_enum_dict_keys_constant_is_exact() -> None:
    assert frozenset({"value", "label"}) == ENUM_DICT_KEYS


def test_legacy_extractor_alias_no_longer_exists() -> None:
    """ARCH-01f removed the leading-underscore alias.

    The canonical name is :func:`nbsnap.snapshot.coerce.collapse_enum_dict`.
    Pin the deletion so a future contributor cannot accidentally
    re-introduce ``_collapse_enum_dict`` at the legacy location.
    """

    from nbsnap.export import extractor

    assert not hasattr(extractor, "_collapse_enum_dict")
    assert not hasattr(extractor, "_ENUM_DICT_KEYS")
