"""SEC-05a: :func:`nbsnap.http.client._redact_body` behaviour.

Each test isolates one transformation so a regression in any
single redaction path fails individually instead of being masked
by a multi-pattern fixture.
"""

from __future__ import annotations

from nbsnap.http.client import _redact_body


def test_authorization_header_line_is_redacted() -> None:
    body = "GET /api/devices\nAuthorization: Token deadbeef0001\nAccept: */*"
    assert "deadbeef0001" not in _redact_body(body)
    assert "Authorization: <redacted>" in _redact_body(body)


def test_authorization_case_insensitive() -> None:
    body = "authorization: Token cafebabe\n"
    out = _redact_body(body)
    assert "cafebabe" not in out


def test_token_hex_literal_is_masked() -> None:
    body = '{"detail": "bad token Token deadbeef"}'
    out = _redact_body(body)
    assert "deadbeef" not in out
    assert "Token <redacted>" in out


def test_script_block_is_stripped() -> None:
    body = "<html><script>const x='secret-cookie';</script></html>"
    out = _redact_body(body)
    assert "<script" not in out
    assert "secret-cookie" not in out


def test_style_block_is_stripped() -> None:
    body = "<html><style>body{background:url('http://leak.example/log');}</style></html>"
    out = _redact_body(body)
    assert "<style" not in out
    assert "leak.example" not in out


def test_innocuous_body_passes_through() -> None:
    body = '{"error": "missing required field name"}'
    assert _redact_body(body) == body


def test_empty_body_passes_through() -> None:
    assert _redact_body("") == ""
