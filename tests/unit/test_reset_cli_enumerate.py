"""FEAT-37c enumeration tests.

Three things this file pins:

1. `_enumerate_ids` yields every numeric id NetBox lists and
   drops any row whose name or slug appears in `--keep`.
2. `_resolve_scope` falls back to `DEFAULT_SCOPE` when
   `--content-types` is unset, and parses a CSV otherwise.
3. End-to-end through `run_reset_cli`, the dry-run output
   carries the per-content-type "would delete N records" line.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from nbsnap.reset_cli import (
    EXIT_OK,
    _enumerate_ids,
    _resolve_scope,
    run_reset_cli,
)


def _args(**override) -> argparse.Namespace:
    defaults = {
        "url": "https://dest.example/",
        "token": "tok",
        "no_verify_tls": False,
        "content_types": None,
        "keep": [],
        "apply": False,
        "confirmed": False,
        "on_error": "stop",
        "audit_out": None,
    }
    defaults.update(override)
    return argparse.Namespace(**defaults)


def _fake_client(**rows_by_endpoint) -> MagicMock:
    """Build a NetboxHTTP-shaped MagicMock whose get_all returns
    pre-seeded rows for each endpoint."""

    client = MagicMock()
    client.is_source.return_value = False
    client.base_url = "https://dest.example/"

    def fake_get_all(endpoint: str) -> iter:
        return iter(rows_by_endpoint.get(endpoint, []))

    client.get_all.side_effect = fake_get_all
    return client


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "NB_SOURCE_URL",
        "NB_SOURCE_TOKEN",
        "NB_DESTINATION_URL",
        "NB_DESTINATION_TOKEN",
        "NB_URL",
        "NB_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# _enumerate_ids
# ---------------------------------------------------------------------------


def test_enumerate_yields_every_id() -> None:
    """Three rows with ids 1, 2, 3 yield those ids in order."""

    client = _fake_client(
        **{
            "dcim/sites/": [
                {"id": 1, "name": "A"},
                {"id": 2, "name": "B"},
                {"id": 3, "name": "C"},
            ]
        }
    )
    ids = list(_enumerate_ids(client, "dcim/sites/", keep_names=set()))
    assert ids == [1, 2, 3]


def test_enumerate_drops_rows_matching_keep_by_name() -> None:
    """A row whose `name` is in keep_names is excluded."""

    client = _fake_client(
        **{
            "dcim/sites/": [
                {"id": 1, "name": "keep-me"},
                {"id": 2, "name": "drop-me"},
            ]
        }
    )
    ids = list(_enumerate_ids(client, "dcim/sites/", keep_names={"keep-me"}))
    assert ids == [2]


def test_enumerate_drops_rows_matching_keep_by_slug() -> None:
    """Some content types only have `slug` in brief responses,
    so the matcher checks slug too."""

    client = _fake_client(
        **{
            "dcim/sites/": [
                {"id": 5, "slug": "hall-d"},
                {"id": 6, "slug": "hall-a"},
            ]
        }
    )
    ids = list(_enumerate_ids(client, "dcim/sites/", keep_names={"hall-d"}))
    assert ids == [6]


def test_enumerate_matches_slug_when_name_field_is_absent() -> None:
    """The slug match works even when there is no `name` key at all.
    NetBox occasionally omits `name` on brief responses for
    slug-keyed content types."""

    client = _fake_client(
        **{
            "dcim/sites/": [
                # Deliberately no `name` key, only `slug`.
                {"id": 11, "slug": "keep-me-slug"},
                {"id": 12, "slug": "drop-me-slug"},
            ]
        }
    )
    ids = list(_enumerate_ids(client, "dcim/sites/", keep_names={"keep-me-slug"}))
    assert ids == [12]


def test_enumerate_skips_rows_without_id() -> None:
    """Defensive: a row missing `id` is silently skipped."""

    client = _fake_client(
        **{
            "dcim/sites/": [
                {"id": 1, "name": "ok"},
                {"name": "missing-id"},  # no id key
                {"id": "not-an-int", "name": "wrong type"},
            ]
        }
    )
    ids = list(_enumerate_ids(client, "dcim/sites/", keep_names=set()))
    assert ids == [1]


# ---------------------------------------------------------------------------
# _resolve_scope
# ---------------------------------------------------------------------------


def test_resolve_scope_defaults_to_default_scope_when_unset() -> None:
    from nbsnap.export.driver import DEFAULT_SCOPE

    assert _resolve_scope(None) == set(DEFAULT_SCOPE)


def test_resolve_scope_parses_csv() -> None:
    assert _resolve_scope("dcim.site,dcim.device") == {"dcim.site", "dcim.device"}


def test_resolve_scope_trims_whitespace_and_drops_empties() -> None:
    assert _resolve_scope("  dcim.site , , dcim.device  ") == {
        "dcim.site",
        "dcim.device",
    }


# ---------------------------------------------------------------------------
# Integration: the dry-run line in run_reset_cli
# ---------------------------------------------------------------------------


def test_dry_run_emits_would_delete_count_per_content_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The dry-run path enumerates each in-scope content type
    and writes one stderr line per type with the count."""

    client = _fake_client(
        **{
            "dcim/sites/": [{"id": 1, "name": "Hall-A"}, {"id": 2, "name": "Hall-B"}],
        }
    )
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=client):
        rc = run_reset_cli(_args(content_types="dcim.site"))

    assert rc == EXIT_OK
    err = capsys.readouterr().err
    assert "dcim.site: would delete 2 records" in err


def test_apply_mode_emits_deleting_count_per_content_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The apply-and-confirmed path uses the progress-paired
    opening line `<ct>: N records to delete` (FEAT-50). Dry-run
    keeps the legacy `would delete` phrasing."""

    client = _fake_client(
        **{
            "dcim/sites/": [{"id": 1, "name": "Hall-A"}],
        }
    )
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=client):
        rc = run_reset_cli(
            _args(
                content_types="dcim.site",
                apply=True,
                confirmed=True,
            )
        )

    assert rc == EXIT_OK
    err = capsys.readouterr().err
    assert "dcim.site: 1 records to delete" in err


def test_keep_excludes_named_row_in_run_reset_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: --keep prunes the count surfaced in the
    stderr summary."""

    client = _fake_client(
        **{
            "dcim/sites/": [
                {"id": 1, "name": "keep-me"},
                {"id": 2, "name": "drop-me"},
            ],
        }
    )
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=client):
        run_reset_cli(_args(content_types="dcim.site", keep=["keep-me"]))

    err = capsys.readouterr().err
    assert "dcim.site: would delete 1 records" in err
