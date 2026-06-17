"""ARCH-05a: :class:`ContentType` parses, renders, and resolves endpoints.

Three behaviours are pinned:

* Round-trip through ``from_str`` -> ``as_str``.
* Endpoint mapping for representative content types.
* :class:`InvalidContentTypeError` for the obvious bad-input shapes.
"""

from __future__ import annotations

import pytest

from nbsnap.schema.content_type import ContentType, InvalidContentTypeError


def test_from_str_round_trip_dcim_device() -> None:
    ct = ContentType.from_str("dcim.device")
    assert ct.app == "dcim"
    assert ct.model == "device"
    assert ct.as_str() == "dcim.device"


def test_endpoint_for_known_content_types() -> None:
    assert ContentType.from_str("dcim.device").endpoint() == "dcim/devices/"
    assert ContentType.from_str("ipam.iprange").endpoint() == "ipam/ip-ranges/"
    assert ContentType.from_str("dcim.cable").endpoint() == "dcim/cables/"


def test_equality_and_hashing_for_dict_key_use() -> None:
    """The frozen dataclass produces hashable instances that compare equal."""

    a = ContentType.from_str("dcim.site")
    b = ContentType.from_str("dcim.site")
    assert a == b
    assert hash(a) == hash(b)
    assert {a: "x"}[b] == "x"


def test_every_endpoint_round_trips_through_from_str() -> None:
    """ARCH-05b: every known content type in ``_ENDPOINTS`` is parseable.

    A regression that adds an entry to the dict but with a malformed
    key (missing dot, multi-dot) would fail here before reaching a
    caller. Pin the contract.
    """

    from nbsnap.schema.content_type import _ENDPOINTS

    for raw in _ENDPOINTS:
        ct = ContentType.from_str(raw)
        assert ct.endpoint() == _ENDPOINTS[raw]


def test_natkey_verify_re_export_is_the_same_dict() -> None:
    """ARCH-05b shim: the legacy ``CONTENT_TYPE_ENDPOINTS`` name still works.

    ARCH-05e drops this re-export; until then, both names must
    resolve to the same in-memory dict so a caller mid-migration
    sees consistent data.
    """

    from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS as legacy
    from nbsnap.schema.content_type import _ENDPOINTS

    assert legacy is _ENDPOINTS


@pytest.mark.parametrize(
    "raw",
    ["dcim", "dcim.devic", "dcim.devic.extra", "", ".device", "dcim."],
)
def test_invalid_content_type_raises(raw: str) -> None:
    with pytest.raises(InvalidContentTypeError) as exc:
        ContentType.from_str(raw)
    assert exc.value.raw == raw
