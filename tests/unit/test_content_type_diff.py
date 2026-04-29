"""FEAT-03b content-type diff tests."""

from __future__ import annotations

from nbsnap.schema.content_types import ContentTypeCache, format_delta_for_operator


def test_diff_splits_into_three_sets() -> None:
    source = ContentTypeCache(
        forward={("dcim", "device"): 1, ("netbox_bgp", "bgpsession"): 2},
        endpoint_used="extras/object-types/",
    )
    dest = ContentTypeCache(
        forward={("dcim", "device"): 1, ("dcim", "cable"): 3},
        endpoint_used="extras/object-types/",
    )
    delta = source.diff(dest)
    assert delta.only_on_source == {("netbox_bgp", "bgpsession")}
    assert delta.only_on_destination == {("dcim", "cable")}
    assert delta.common == {("dcim", "device")}


def test_format_delta_renders_sections() -> None:
    source = ContentTypeCache(
        forward={("dcim", "device"): 1},
        endpoint_used="extras/object-types/",
    )
    dest = ContentTypeCache(
        forward={("dcim", "device"): 1},
        endpoint_used="extras/object-types/",
    )
    text = format_delta_for_operator(source.diff(dest))
    assert "only on source" in text
    assert "only on destination" in text
    assert "common: 1" in text
