"""ARCH-05g: a parametrised regression on common content-type typos.

The audit's headline win for ARCH-05 is catching typos like
``dcim.devic`` at parse time. This file enumerates the operator-
observed typos and asserts each raises :class:`InvalidContentTypeError`.

If we ever add a typo here that DOES exist (because the registry
grew to cover it), the test will fail loudly so we can either
update the assertion or revisit whether that key really belongs in
``_ENDPOINTS``.
"""

from __future__ import annotations

import pytest

from nbsnap.schema.content_type import ContentType, InvalidContentTypeError


_TYPOS = [
    "dcim.devic",
    "dcim.devices",
    "dcim.sites",
    "ipam.ipranges",
    "ipam.iprange1",
    "dcim.cable_",
    "Dcim.Device",
    "dcim.Device",
    " dcim.device",
    "dcim.device ",
    "dcim..device",
    "dcim.",
    ".device",
    "..",
    "dcim.device.",
    "dcim_device",
]


@pytest.mark.parametrize("raw", _TYPOS, ids=lambda x: repr(x))
def test_typo_raises_invalid_content_type_error(raw: str) -> None:
    with pytest.raises(InvalidContentTypeError):
        ContentType.from_str(raw)


def test_canonical_form_still_works() -> None:
    """A sanity assertion alongside the typo set so a regression in
    :meth:`ContentType.from_str` that broke every input would fail here
    instead of silently passing the typo tests too.
    """

    assert ContentType.from_str("dcim.device").as_str() == "dcim.device"
