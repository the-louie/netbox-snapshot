"""ARCH-05d: :class:`NKRegistry` accepts ``str`` or ``ContentType`` keys.

The registry's internal storage is still keyed on strings, but
``get`` and ``has`` accept either form so the broader codebase can
migrate to typed keys one call-site at a time.
"""

from __future__ import annotations

import pytest

from nbsnap.natkey.model import NKField, NKRegistry, NKSpec, Strategy
from nbsnap.schema.content_type import ContentType


def _registry_with_site() -> NKRegistry:
    r = NKRegistry()
    r.register(NKSpec("dcim.site", Strategy.SLUG, (NKField("slug"),)))
    return r


def test_get_accepts_string_key() -> None:
    r = _registry_with_site()
    spec = r.get("dcim.site")
    assert spec.content_type == "dcim.site"


def test_get_accepts_content_type_key() -> None:
    r = _registry_with_site()
    spec = r.get(ContentType.from_str("dcim.site"))
    assert spec.content_type == "dcim.site"


def test_has_accepts_both_forms() -> None:
    r = _registry_with_site()
    assert r.has("dcim.site")
    assert r.has(ContentType.from_str("dcim.site"))
    assert not r.has("dcim.devic")
    assert not r.has(ContentType(app="dcim", model="devic"))


def test_unsupported_key_type_raises_type_error() -> None:
    r = _registry_with_site()
    with pytest.raises(TypeError):
        r.get(42)  # type: ignore[arg-type]
