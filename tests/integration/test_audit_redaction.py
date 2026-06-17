"""SEC-05c: token bytes do not reach audit.jsonl.

A failing POST whose 4xx body echoes ``Authorization: Token deadbeef0001``
must not write that token to the audit log. SEC-05a/b moved the
redaction onto :class:`NetboxHTTPError`'s construction, which means
every consumer (auditor, stderr, debug summary) sees the
sanitised body.

The integration version uses the in-process auditor rather than a
subprocess; the destination-stack-free variant is enough because
the chokepoint is the exception's ``body`` attribute, not the
import driver.
"""

from __future__ import annotations

import json
from pathlib import Path

from nbsnap.http.client import NetboxHTTPError
from nbsnap.import_.audit import Auditor, DropCategory, DropEvent


def test_token_in_response_body_does_not_reach_audit_jsonl(tmp_path: Path) -> None:
    """Construct a NetboxHTTPError with a token-shaped body, project
    it into a DropEvent, write the auditor's JSONL, and assert the
    token does not appear on disk.
    """

    raw_body = '{"detail": "bad", "auth_header": "Authorization: Token deadbeef0001"}'
    err = NetboxHTTPError(
        "POST",
        "https://dest.example/api/dcim/devices/",
        400,
        raw_body,
    )

    # SEC-05b already sanitises err.body. We pipe it through the
    # auditor as a forensic record.
    auditor = Auditor()
    auditor.record(
        DropEvent(
            category=DropCategory.UPSERT_FAILED,
            child_content_type="dcim.device",
            child_nk=("d39a",),
            field_name="primary_ip4",
            target_content_type="",
            target_nk=(),
            message=err.body,
        )
    )

    out = tmp_path / "audit.jsonl"
    auditor.write_jsonl(out)

    text = out.read_text(encoding="utf-8")
    assert "deadbeef0001" not in text, (
        "the token must not reach audit.jsonl; SEC-05b sanitises at "
        f"NetboxHTTPError construction. audit.jsonl contained: {text!r}"
    )
    # The audit-row container shape stays intact, so the operator
    # still sees that an UPSERT_FAILED event landed.
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert any(row["category"] == "upsert_failed" for row in rows)
