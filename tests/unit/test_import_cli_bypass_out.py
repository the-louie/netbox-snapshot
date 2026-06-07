"""SEC-06a: ``--bypass-out`` and the default-inside-snapshot rule.

The CLI now exposes ``--bypass-out PATH`` so the preflight-bypass
detail file (FEAT-47) can be redirected explicitly, and its default
location moved from ``audit_path.with_name("preflight-bypass.jsonl")``
to ``<snapshot_dir>/preflight-bypass.jsonl``. The pinning matters
because the previous behaviour caused the bypass to follow
``--audit-out`` to a filesystem outside the snapshot, separating
the forensic record from the artefact it described.

These tests only exercise the argparse layer, the actual write of
the bypass JSONL is covered by the existing import integration
tests.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from nbsnap.import_cli import add_import_args


def _parse(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_import_args(parser)
    return parser.parse_args(args)


def test_bypass_out_defaults_to_none_so_runtime_derives_inside_snapshot() -> None:
    """No ``--bypass-out`` means the runtime falls back to ``<snapshot_dir>/preflight-bypass.jsonl``.

    The defaulting happens inside ``main`` (``args.bypass_out or
    (in_dir / 'preflight-bypass.jsonl')``); argparse reports ``None``
    so the call-site fallback can detect the unset case.
    """

    ns = _parse(["--url", "https://x/", "--token", "tok", "--in", "."])
    assert ns.bypass_out is None


def test_explicit_bypass_out_path_is_respected() -> None:
    """An explicit ``--bypass-out`` produces the literal path on the namespace."""

    ns = _parse(
        [
            "--url",
            "https://x/",
            "--token",
            "tok",
            "--in",
            ".",
            "--bypass-out",
            "/tmp/x.jsonl",
        ]
    )
    assert ns.bypass_out == Path("/tmp/x.jsonl")


def test_audit_out_and_bypass_out_resolve_independently() -> None:
    """Setting both flags does not produce a path collision.

    Pinning the independence of the two flags is the SEC-06a
    correctness property. If a future refactor accidentally re-
    couples the bypass path to ``--audit-out`` this test fails.
    """

    ns = _parse(
        [
            "--url",
            "https://x/",
            "--token",
            "tok",
            "--in",
            ".",
            "--audit-out",
            "/tmp/audit.jsonl",
            "--bypass-out",
            "/tmp/bypass.jsonl",
        ]
    )
    assert ns.audit_out == Path("/tmp/audit.jsonl")
    assert ns.bypass_out == Path("/tmp/bypass.jsonl")
    assert ns.audit_out != ns.bypass_out
