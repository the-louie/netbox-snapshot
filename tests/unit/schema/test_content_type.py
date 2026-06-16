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


@pytest.mark.parametrize(
    "raw",
    ["dcim", "dcim.devic", "dcim.devic.extra", "", ".device", "dcim."],
)
def test_invalid_content_type_raises(raw: str) -> None:
    with pytest.raises(InvalidContentTypeError) as exc:
        ContentType.from_str(raw)
    assert exc.value.raw == raw
