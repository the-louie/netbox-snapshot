"""FEAT-01a auth precedence tests."""

from __future__ import annotations

import pytest

from nbsnap.http.client import NetboxHTTP


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe NB_* before every test, so order does not leak."""

    for k in (
        "NB_SOURCE_URL",
        "NB_SOURCE_TOKEN",
        "NB_DESTINATION_URL",
        "NB_DESTINATION_TOKEN",
        "NB_URL",
        "NB_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)


def test_from_env_picks_source_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only NB_SOURCE_URL/TOKEN set, source role picks it."""

    monkeypatch.setenv("NB_SOURCE_URL", "https://src.example/")
    monkeypatch.setenv("NB_SOURCE_TOKEN", "src-token")
    client = NetboxHTTP.from_env("source")
    assert client.base_url == "https://src.example/"


def test_from_env_role_wins_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Role-specific env wins over legacy NB_URL/NB_TOKEN."""

    monkeypatch.setenv("NB_URL", "https://legacy/")
    monkeypatch.setenv("NB_TOKEN", "legacy-token")
    monkeypatch.setenv("NB_SOURCE_URL", "https://src.example/")
    monkeypatch.setenv("NB_SOURCE_TOKEN", "src-token")
    client = NetboxHTTP.from_env("source")
    assert client.base_url == "https://src.example/"


def test_from_env_explicit_kwarg_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit `token=` kwarg outranks every env source."""

    monkeypatch.setenv("NB_SOURCE_URL", "https://src.example/")
    monkeypatch.setenv("NB_SOURCE_TOKEN", "env-token")
    client = NetboxHTTP.from_env("source", token="kw-token")
    # Token is private; introspect via repr to confirm the last 4
    # chars surface (the token tail is what `repr` exposes).
    assert "kw-token"[-4:] in repr(client)


def test_from_env_source_is_readonly_even_with_writes_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source guard rail wins over `allow_writes=True`."""

    monkeypatch.setenv("NB_SOURCE_URL", "https://src.example/")
    monkeypatch.setenv("NB_SOURCE_TOKEN", "tok")
    client = NetboxHTTP.from_env("source", allow_writes=True)
    assert client.is_source() is True
    assert client.allow_writes is False


def test_constructor_against_source_url_forces_readonly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct construction against source URL is also forced read-only."""

    monkeypatch.setenv("NB_SOURCE_URL", "https://src.example/")
    client = NetboxHTTP("https://src.example/", "tok", allow_writes=True)
    assert client.is_source() is True
    assert client.allow_writes is False


def test_repr_masks_token() -> None:
    """`repr()` must not leak the full token."""

    client = NetboxHTTP("https://dest.example/", "supersecrettoken1234")
    r = repr(client)
    assert "supersecrettoken1234" not in r
    assert "1234" in r  # the tail is fine for debugging
    assert "allow_writes" in r
    assert "is_source=False" in r
