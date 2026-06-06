"""SEC-05b: ``NetboxHTTPError.body`` is sanitised at construction.

If the redaction only fired when somebody called ``_redact_body``
explicitly we would have to police every consumer (audit log,
stderr printer, debug-summary serialiser) individually. Doing the
redaction in :meth:`NetboxHTTPError.__init__` makes it impossible
to read an un-redacted body off the exception, no matter how the
caller accesses it.
"""

from __future__ import annotations

from nbsnap.http.client import NetboxHTTPError


def test_body_is_sanitised_on_construction() -> None:
    raw = "Authorization: Token deadbeef0001\nResponse: {}"
    err = NetboxHTTPError("POST", "https://dest.example/api/x/", 500, raw)

    assert "deadbeef0001" not in err.body
    assert "Authorization: <redacted>" in err.body


def test_str_of_error_contains_no_token_bytes() -> None:
    """The string representation is what reaches stderr and audit.jsonl.

    Even if a future consumer pulls ``str(error)`` instead of
    ``error.body`` directly, the token must not appear in the
    rendered message either.
    """

    # Use a hex string: real NetBox tokens are 40-char hex, the
    # redaction regex matches Token\s+[0-9a-fA-F]+ so the test
    # fixture must be hex-shaped to exercise it.
    raw = '{"detail": "bad", "echo": "Token deadbeefcafe1234"}'
    err = NetboxHTTPError("POST", "https://dest.example/api/x/", 400, raw)

    assert "deadbeefcafe1234" not in str(err)
    assert "Token <redacted>" in str(err)


def test_innocuous_body_unchanged() -> None:
    """A normal 4xx body without secret-shaped content is left alone."""

    raw = '{"detail": "missing field name"}'
    err = NetboxHTTPError("POST", "https://dest.example/api/x/", 400, raw)
    assert err.body == raw
