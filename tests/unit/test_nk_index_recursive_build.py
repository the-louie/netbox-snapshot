"""FEAT-36i tests for recursive NKIndex.ensure_built."""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.import_.nk_index import NKIndex
from nbsnap.natkey.model import NKField, NKRegistry, NKSpec, Strategy
from nbsnap.natkey.registry import default as default_registry


def _http_recording(call_order: list[str]) -> MagicMock:
    """Return a fake NetboxHTTP whose `get_all` records every
    endpoint it touches and yields nothing back."""

    http = MagicMock()

    def fake(endpoint: str):
        call_order.append(endpoint.split("?")[0])
        return iter([])

    http.get_all.side_effect = fake
    return http


def test_building_ipaddress_visits_ipaddress_endpoint() -> None:
    """`ensure_built('ipam.ipaddress')` issues the list call
    against the IPAddress endpoint at minimum."""

    calls: list[str] = []
    http = _http_recording(calls)
    idx = NKIndex()
    idx.ensure_built(http, default_registry(), "ipam.ipaddress")
    assert "ipam/ip-addresses/" in calls


def test_recursive_build_visits_parent_endpoints_first() -> None:
    """When a NKSpec's field carries `parent_content_type`, the
    parent's endpoint is hit BEFORE the child's so the nested
    resolve() call inside the child build sees a populated
    parent index."""

    reg = NKRegistry()
    # Parent CT: dcim.site, slug-based.
    reg.register(
        NKSpec(
            "dcim.site",
            Strategy.SLUG,
            (NKField("slug"),),
        )
    )
    # Child CT: dcim.location, composite (site, slug); site is
    # itself an NK against dcim.site.
    reg.register(
        NKSpec(
            "dcim.location",
            Strategy.COMPOSITE,
            (NKField("site", "dcim.site"), NKField("slug")),
        )
    )

    calls: list[str] = []
    http = _http_recording(calls)
    idx = NKIndex()
    idx.ensure_built(http, reg, "dcim.location")

    # Both endpoints visited.
    assert any("sites/" in c for c in calls)
    assert any("locations/" in c for c in calls)
    # And the parent (site) was visited before the child
    # (location).
    site_idx = next(i for i, c in enumerate(calls) if "sites/" in c)
    loc_idx = next(i for i, c in enumerate(calls) if "locations/" in c)
    assert site_idx < loc_idx


def test_self_referencing_nkspec_does_not_loop() -> None:
    """`dcim.devicerole.parent -> dcim.devicerole` would loop
    forever without the `_building` cycle guard. With the
    guard, the endpoint is visited exactly once."""

    reg = NKRegistry()
    reg.register(
        NKSpec(
            "dcim.devicerole",
            Strategy.COMPOSITE,
            (NKField("parent", "dcim.devicerole"), NKField("slug")),
        )
    )

    calls: list[str] = []
    http = _http_recording(calls)
    idx = NKIndex()
    idx.ensure_built(http, reg, "dcim.devicerole")

    assert len([c for c in calls if "device-roles" in c]) == 1


def test_repeated_ensure_built_is_a_noop() -> None:
    """A second call against the same content type does not
    re-issue the list call; the index already covers it."""

    calls: list[str] = []
    http = _http_recording(calls)
    idx = NKIndex()
    idx.ensure_built(http, default_registry(), "dcim.site")
    n = len(calls)
    idx.ensure_built(http, default_registry(), "dcim.site")
    assert len(calls) == n


def test_partially_built_chain_still_builds_remaining_levels() -> None:
    """If the parent index is already built but the child is
    not, only the child endpoint is hit."""

    reg = NKRegistry()
    reg.register(
        NKSpec(
            "dcim.site",
            Strategy.SLUG,
            (NKField("slug"),),
        )
    )
    reg.register(
        NKSpec(
            "dcim.location",
            Strategy.COMPOSITE,
            (NKField("site", "dcim.site"), NKField("slug")),
        )
    )

    calls: list[str] = []
    http = _http_recording(calls)
    idx = NKIndex()
    # First, build only the parent.
    idx.ensure_built(http, reg, "dcim.site")
    pre = len(calls)
    # Then build the child; only the child endpoint should fire.
    idx.ensure_built(http, reg, "dcim.location")
    new_calls = calls[pre:]
    assert all("sites/" not in c for c in new_calls)
    assert any("locations/" in c for c in new_calls)


def test_records_with_resolvable_nks_land_in_the_index() -> None:
    """When `http.get_all` actually yields rows, each row whose
    NK resolves cleanly gets indexed by `(ct, NK)`."""

    http = MagicMock()
    http.get_all.return_value = iter(
        [
            {"id": 7, "slug": "hall-a", "name": "Hall A"},
            {"id": 8, "slug": "hall-b", "name": "Hall B"},
        ]
    )

    reg = NKRegistry()
    reg.register(
        NKSpec(
            "dcim.site",
            Strategy.SLUG,
            (NKField("slug"),),
        )
    )

    idx = NKIndex()
    idx.ensure_built(http, reg, "dcim.site")
    assert idx.lookup("dcim.site", ("hall-a",)) == 7
    assert idx.lookup("dcim.site", ("hall-b",)) == 8


def test_simple_nk_content_type_still_uses_brief() -> None:
    """Companion to the composite case below: a content type
    whose NKSpec has no `parent_content_type` field keeps the
    `brief=true` optimisation. Asserting this guards against a
    future refactor that drops `brief=true` everywhere "to be
    safe", which would silently widen the payload on every
    index population.
    """

    seen_urls: list[str] = []

    def fake(endpoint: str):
        seen_urls.append(endpoint)
        return iter([])

    http = MagicMock()
    http.get_all.side_effect = fake

    reg = NKRegistry()
    reg.register(NKSpec("dcim.site", Strategy.SLUG, (NKField("slug"),)))

    NKIndex().ensure_built(http, reg, "dcim.site")

    assert any("brief=true" in u for u in seen_urls), (
        "simple-NK listings must still request brief=true for payload size; "
        f"saw {seen_urls}"
    )


def test_composite_nk_content_type_is_listed_without_brief() -> None:
    """BUG-15 regression. NetBox's `brief=true` response omits
    nested fields like `site` from a Location row. The NK index
    needs those nested fields to compute composite NKs, so the
    request must drop `brief=true` for any content type whose
    NKSpec carries a `parent_content_type` field. Otherwise the
    nested `resolve()` call raises and every row is silently
    dropped from the index, which is what the second
    `nbsnap import` was tripping on (`NK not found on
    destination`).
    """

    seen_urls: list[str] = []

    def fake(endpoint: str):
        seen_urls.append(endpoint)
        return iter([])

    http = MagicMock()
    http.get_all.side_effect = fake

    reg = NKRegistry()
    reg.register(NKSpec("dcim.site", Strategy.SLUG, (NKField("slug"),)))
    reg.register(
        NKSpec(
            "dcim.location",
            Strategy.COMPOSITE,
            (NKField("site", "dcim.site"), NKField("slug")),
        )
    )

    NKIndex().ensure_built(http, reg, "dcim.location")

    location_urls = [u for u in seen_urls if "locations/" in u]
    assert location_urls, "the location endpoint was never listed"
    assert all(
        "brief=true" not in u for u in location_urls
    ), f"composite-NK listing must not request brief=true, saw {location_urls}"


def test_composite_nk_rows_land_in_the_index_after_listing() -> None:
    """BUG-15 regression. Once the listing returns full rows
    (with the nested `site` payload), the composite NK is
    computed and the row is reachable through `lookup`."""

    reg = NKRegistry()
    reg.register(NKSpec("dcim.site", Strategy.SLUG, (NKField("slug"),)))
    reg.register(
        NKSpec(
            "dcim.location",
            Strategy.COMPOSITE,
            (NKField("site", "dcim.site"), NKField("name")),
        )
    )

    def fake(endpoint: str):
        if "sites/" in endpoint:
            return iter([{"id": 1, "slug": "hall-d"}])
        if "locations/" in endpoint:
            # Full (non-brief) shape: `site` is a nested dict.
            return iter(
                [
                    {
                        "id": 99,
                        "name": "the-forge",
                        "site": {"id": 1, "slug": "hall-d"},
                    },
                ]
            )
        return iter([])

    http = MagicMock()
    http.get_all.side_effect = fake

    idx = NKIndex()
    idx.ensure_built(http, reg, "dcim.location")

    assert idx.lookup("dcim.location", (("hall-d",), "the-forge")) == 99
