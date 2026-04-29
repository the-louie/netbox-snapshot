"""Unit tests for the source-read-only guard helpers (FEAT-01g1)."""

from __future__ import annotations

import pytest

from nbsnap.http.guard import READ_ONLY_VERBS, SourceWriteForbidden, is_source_url


@pytest.fixture()
def source_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin NB_SOURCE_URL to a known value for the duration of a test."""

    url = "https://host.docker.internal:8443"
    monkeypatch.setenv("NB_SOURCE_URL", url)
    return url


def test_is_source_url_matches_exact_url(source_env: str) -> None:
    """Bare source URL is recognised as the source."""

    assert is_source_url(source_env) is True


def test_is_source_url_matches_with_trailing_slash(source_env: str) -> None:  # noqa: ARG001
    """A trailing slash does not let the caller bypass the guard."""

    assert is_source_url("https://host.docker.internal:8443/") is True


def test_is_source_url_matches_with_api_path(source_env: str) -> None:  # noqa: ARG001
    """An `/api/...` suffix does not bypass the guard."""

    assert is_source_url("https://host.docker.internal:8443/api/dcim/devices/") is True


def test_is_source_url_rejects_other_host(source_env: str) -> None:  # noqa: ARG001
    """A different host (even same port) is not the source."""

    assert is_source_url("https://other-host:8443/") is False


def test_is_source_url_returns_false_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset NB_SOURCE_URL and no explicit override means False (destination case)."""

    monkeypatch.delenv("NB_SOURCE_URL", raising=False)
    assert is_source_url("https://anything/") is False


def test_is_source_url_accepts_explicit_override() -> None:
    """`source_url` kwarg lets tests pin the comparison without env."""

    assert is_source_url("https://pinned/", source_url="https://pinned/") is True


def test_source_write_forbidden_message_contains_verb_and_url() -> None:
    """The exception message must surface both the verb and the URL."""

    exc = SourceWriteForbidden("POST", "https://h/x")
    msg = str(exc)
    assert "POST" in msg
    assert "https://h/x" in msg


def test_read_only_verbs_set_contains_get_head_options() -> None:
    """Verbs the source can see are exactly GET, HEAD, OPTIONS."""

    assert {"GET", "HEAD", "OPTIONS"} == READ_ONLY_VERBS
