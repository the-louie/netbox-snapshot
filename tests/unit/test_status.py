"""FEAT-04a status fetcher tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.schema.status import Status


def test_fetch_populates_dataclass() -> None:
    http = MagicMock()
    http.get_one.side_effect = [
        {
            "netbox-version": "4.6.2",
            "python-version": "3.11.6",
            "installed-apps": ["dcim", "ipam"],
        },
        [
            {"name": "netbox_bgp", "version": "0.13.0"},
        ],
    ]
    status = Status.fetch(http)
    assert status.netbox_version == "4.6.2"
    assert status.python_version == "3.11.6"
    assert status.installed_apps == ["dcim", "ipam"]
    assert len(status.plugins) == 1
    assert status.plugins[0].name == "netbox_bgp"
    assert status.plugins[0].version == "0.13.0"


def test_fetch_tolerates_missing_fields() -> None:
    http = MagicMock()
    http.get_one.side_effect = [{}, {}]
    status = Status.fetch(http)
    assert status.netbox_version == "unknown"
    assert status.python_version == "unknown"
    assert status.installed_apps == []
    assert status.plugins == []


def test_fetch_accepts_underscore_field_names() -> None:
    """NetBox 4.1+ emits `netbox_version`, not `netbox-version`."""

    http = MagicMock()
    http.get_one.side_effect = [
        {
            "netbox_version": "4.6.2",
            "python_version": "3.11.6",
            "installed_apps": {"django_filters": "25.2"},
        },
        [],
    ]
    status = Status.fetch(http)
    assert status.netbox_version == "4.6.2"
    assert status.python_version == "3.11.6"
    assert status.installed_apps == ["django_filters@25.2"]


def test_fetch_accepts_hyphen_field_names() -> None:
    """NetBox 3.x and early 4.x emit `netbox-version` (hyphen)."""

    http = MagicMock()
    http.get_one.side_effect = [
        {
            "netbox-version": "3.7.2",
            "python-version": "3.10.0",
            "installed-apps": ["django_filters@22.0"],
        },
        [],
    ]
    status = Status.fetch(http)
    assert status.netbox_version == "3.7.2"
    assert status.installed_apps == ["django_filters@22.0"]
