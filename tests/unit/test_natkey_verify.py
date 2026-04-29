"""FEAT-10a verify (duplicate-NK audit) tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from nbsnap.natkey.verify import audit


def _fake_http_with(rows_by_endpoint: dict[str, list[dict]]) -> MagicMock:
    http = MagicMock()

    def fake_get_all(path: str):
        return iter(rows_by_endpoint.get(path, []))

    http.get_all.side_effect = fake_get_all
    return http


def test_audit_no_duplicates_is_clean() -> None:
    http = _fake_http_with(
        {
            "dcim/sites/": [
                {"id": 1, "slug": "hall-d"},
                {"id": 2, "slug": "hall-e"},
            ]
        }
    )
    report = audit(http)
    assert report.is_clean()
    assert report.by_ct["dcim.site"] == 2


def test_audit_reports_duplicate_slug() -> None:
    http = _fake_http_with(
        {
            "dcim/sites/": [
                {"id": 1, "slug": "hall-d"},
                {"id": 2, "slug": "hall-d"},  # duplicate
            ]
        }
    )
    report = audit(http)
    assert not report.is_clean()
    assert len(report.duplicates) == 1
    finding = report.duplicates[0]
    assert finding.content_type == "dcim.site"
    assert finding.natural_key == ("hall-d",)
    assert set(finding.record_ids) == {1, 2}
