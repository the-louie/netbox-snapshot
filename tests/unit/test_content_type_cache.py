"""FEAT-03a content-type cache tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nbsnap.schema.content_types import ContentTypeCache


def test_id_for_returns_known_mapping() -> None:
    cache = ContentTypeCache(
        forward={("dcim", "device"): 17, ("ipam", "prefix"): 21},
        endpoint_used="extras/object-types/",
    )
    assert cache.id_for("dcim", "device") == 17


def test_natural_for_returns_known_id() -> None:
    cache = ContentTypeCache(
        forward={("dcim", "device"): 17},
        endpoint_used="extras/object-types/",
    )
    assert cache.natural_for(17) == ("dcim", "device")


def test_unknown_lookup_raises_key_error() -> None:
    cache = ContentTypeCache(forward={}, endpoint_used="extras/object-types/")
    with pytest.raises(KeyError):
        cache.id_for("zzz", "zzz")


def test_iterable_over_triples() -> None:
    cache = ContentTypeCache(
        forward={("dcim", "device"): 1, ("ipam", "vlan"): 2},
        endpoint_used="extras/object-types/",
    )
    triples = sorted(cache)
    assert triples == [("dcim", "device", 1), ("ipam", "vlan", 2)]


def test_fetch_prefers_modern_endpoint_first() -> None:
    """NetBox 4.1+ install: `core/object-types/` is the first probe."""

    http = MagicMock()
    http.get_all.return_value = iter(
        [{"app_label": "dcim", "model": "device", "id": 17}]
    )

    cache = ContentTypeCache.fetch(http)
    assert cache.endpoint_used == "core/object-types/"
    assert cache.id_for("dcim", "device") == 17


def test_fetch_falls_back_through_legacy_paths() -> None:
    """Old NetBox 3.x install: `core/object-types/` 404s, the cache
    falls through `extras/object-types/` to `extras/content-types/`.
    """

    from nbsnap.http.client import NetboxHTTPError

    http = MagicMock()

    def fake_get_all(endpoint: str):
        if endpoint != "extras/content-types/":
            raise NetboxHTTPError("GET", endpoint, 404, "not found")
        return iter([{"app_label": "dcim", "model": "device", "id": 17}])

    http.get_all.side_effect = fake_get_all
    cache = ContentTypeCache.fetch(http)
    assert cache.endpoint_used == "extras/content-types/"
    assert cache.id_for("dcim", "device") == 17
