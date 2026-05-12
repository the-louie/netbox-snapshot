"""Regression tests for the NetBox enum-dict serialisation fix.

Background. NetBox 4.x returns choice fields like `status` as
`{"value": "active", "label": "Active"}` on GET, but the same
field on POST/PATCH must be the bare string `"active"`. Without
the collapse the import side gets HTTP 400 on every record that
carries a choice field.

The fix lives in `_collapse_enum_dict` and is invoked from
`_apply_allowlist`. This test file pins both the helper's exact
behaviour AND the inline-via-allowlist path so a future
refactor cannot regress the import path silently.
"""

from __future__ import annotations

from nbsnap.export.extractor import _apply_allowlist, _collapse_enum_dict

# ---------------------------------------------------------------------------
# _collapse_enum_dict, the helper itself
# ---------------------------------------------------------------------------


def test_collapses_classic_status_enum_dict() -> None:
    """The canonical NetBox enum-dict shape collapses to its value."""

    enum_dict = {"value": "active", "label": "Active"}
    assert _collapse_enum_dict(enum_dict) == "active"


def test_collapses_airflow_enum_dict() -> None:
    """Same fix covers every choice field, not just status.

    `airflow` is the canonical second example we observed on
    devices during the rescue.
    """

    enum_dict = {"value": "front-to-rear", "label": "Front to rear"}
    assert _collapse_enum_dict(enum_dict) == "front-to-rear"


def test_passes_through_non_dict_values() -> None:
    """Plain strings, ints, None, and lists are untouched."""

    assert _collapse_enum_dict("active") == "active"
    assert _collapse_enum_dict(42) == 42
    assert _collapse_enum_dict(None) is None
    assert _collapse_enum_dict([]) == []
    assert _collapse_enum_dict(["a", "b"]) == ["a", "b"]


def test_does_not_collapse_nested_object_dicts() -> None:
    """A regular nested object (e.g. a Site nested in a Device)
    has more than two keys, so the collapser leaves it alone."""

    nested_site = {"id": 7, "name": "Hall-D", "slug": "hall-d", "url": "..."}
    assert _collapse_enum_dict(nested_site) == nested_site


def test_does_not_collapse_dict_with_value_but_extra_keys() -> None:
    """Frozen-set equality on the key set guards against false
    positives. A payload with `value` AND extra fields is not an
    enum-dict, leave it alone."""

    rich_dict = {"value": "x", "label": "X", "extra": 1}
    assert _collapse_enum_dict(rich_dict) == rich_dict


# ---------------------------------------------------------------------------
# _apply_allowlist, the integration point
# ---------------------------------------------------------------------------


def test_apply_allowlist_collapses_status_inline() -> None:
    """End-to-end check: a Site row's `status` survives the
    allowlist filter AND lands as the bare string."""

    raw_site = {
        "id": 1,
        "name": "Hall-A",
        "slug": "a",
        "status": {"value": "active", "label": "Active"},
        "tenant": None,
    }
    allowlist = frozenset({"name", "slug", "status", "tenant"})

    out = _apply_allowlist(raw_site, allowlist)

    assert out == {
        "name": "Hall-A",
        "slug": "a",
        "status": "active",
        "tenant": None,
    }


def test_apply_allowlist_handles_multiple_enum_fields() -> None:
    """A Device carries `status`, `airflow`, `face`, and a real
    nested FK (`site`). The collapse fires on the three enum
    fields and leaves the nested FK alone."""

    raw_device = {
        "id": 7,
        "name": "d39a",
        "status": {"value": "active", "label": "Active"},
        "airflow": {"value": "front-to-rear", "label": "Front to rear"},
        "face": {"value": "front", "label": "Front"},
        "site": {"id": 1, "name": "Hall-A", "slug": "a"},
    }
    allowlist = frozenset({"name", "status", "airflow", "face", "site"})

    out = _apply_allowlist(raw_device, allowlist)

    assert out["status"] == "active"
    assert out["airflow"] == "front-to-rear"
    assert out["face"] == "front"
    # The nested Site dict has 3 keys, not the enum-dict shape,
    # so it survives intact. Later FK rewriting handles it.
    assert out["site"] == {"id": 1, "name": "Hall-A", "slug": "a"}


def test_apply_allowlist_drops_fields_outside_allowlist() -> None:
    """The collapse does not affect the drop behaviour for
    fields not in the allowlist."""

    raw = {"name": "x", "id": 99, "url": "http://...", "display": "X"}
    allowlist = frozenset({"name"})
    assert _apply_allowlist(raw, allowlist) == {"name": "x"}
