"""FEAT-04b version skew comparator tests."""

from __future__ import annotations

from nbsnap.schema.status import Status, VersionSkew, parse_version


def _status(version: str) -> Status:
    return Status(netbox_version=version, python_version="3.11.6")


def test_parse_version_pads_with_zeros() -> None:
    assert parse_version("4.6") == (4, 6, 0)
    assert parse_version("4") == (4, 0, 0)


def test_parse_version_strips_pre_release_tail() -> None:
    assert parse_version("4.6.2-rc1") == (4, 6, 2)


def test_same_version_is_none_skew() -> None:
    assert _status("4.6.2").version_skew(_status("4.6.2")) == VersionSkew.NONE


def test_patch_skew() -> None:
    assert _status("4.6.2").version_skew(_status("4.6.5")) == VersionSkew.PATCH


def test_minor_skew() -> None:
    assert _status("4.6.2").version_skew(_status("4.7.0")) == VersionSkew.MINOR


def test_major_skew() -> None:
    assert _status("4.6.2").version_skew(_status("5.0.0")) == VersionSkew.MAJOR


def test_allowed_by_accepts_lower_buckets() -> None:
    assert VersionSkew.MINOR.allowed_by(VersionSkew.MINOR) is True
    assert VersionSkew.PATCH.allowed_by(VersionSkew.MINOR) is True
    assert VersionSkew.NONE.allowed_by(VersionSkew.MINOR) is True


def test_allowed_by_rejects_higher_bucket() -> None:
    assert VersionSkew.MAJOR.allowed_by(VersionSkew.MINOR) is False
