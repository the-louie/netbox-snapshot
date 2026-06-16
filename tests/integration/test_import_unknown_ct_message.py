"""ARCH-08c: end-to-end refusal of a manifest with an unknown content type.

Drives :func:`run_import_cli` in-process against a forged snapshot
whose manifest names a content type nbsnap does not recognise.
A subprocess variant would be more realistic but requires nbsnap
to be pip-installed in the test venv; running in-process keeps the
contract identical for the bits ARCH-08c cares about.

What we assert:

1. The CLI returns the non-zero ``EXIT_PREFLIGHT_BLOCKED`` (1).
2. Stderr names the offending content type so the operator can grep.
3. No "destination unreachable" / "TLS failed" message leaks,
   which would suggest the pre-network refusal was bypassed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from nbsnap.import_cli import EXIT_PREFLIGHT_BLOCKED, run_import_cli


def _write_bad_manifest_snapshot(tmp_path: Path) -> Path:
    snap = tmp_path / "snapshot"
    snap.mkdir()
    (snap / "schema").mkdir()
    (snap / "schema" / "openapi.json").write_text(
        json.dumps({"paths": {}, "components": {"schemas": {}}}), encoding="utf-8"
    )
    (snap / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source_url_hash": "abcd1234ef56",
                "netbox_version": "4.6.2",
                "counts": {"dcim.devic": 7, "dcim.site": 1},  # the typo
                "perf": {},
                "deferred_edges": [],
            }
        ),
        encoding="utf-8",
    )
    return snap


def _import_args(snap: Path) -> argparse.Namespace:
    return argparse.Namespace(
        url="https://no-such-host.invalid/",
        token="irrelevant",
        no_verify_tls=False,
        in_dir=snap,
        max_version_skew="minor",
        on_error="continue",
        audit_out=None,
        bypass_out=None,
        plugins_dir=None,
        allow_enum_dict_bypass=False,
        max_parse_errors=0,
        audit_summary_limit=10,
        max_skipped=-1,
        max_skipped_ct=[],
        no_phase2_verify=False,
        audit_fsync=False,
        no_timestamps=True,
        no_lookahead_failure_cache=False,
        strict_schema=False,
        use_destination_schema=False,
    )


def test_unknown_content_type_refused_with_clear_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snap = _write_bad_manifest_snapshot(tmp_path)

    rc = run_import_cli(_import_args(snap))
    err = capsys.readouterr().err

    assert rc == EXIT_PREFLIGHT_BLOCKED, (
        f"expected EXIT_PREFLIGHT_BLOCKED (1), got {rc}; stderr was: {err!r}"
    )
    assert "dcim.devic" in err, (
        "stderr must name the offending content type for the operator; "
        f"got stderr: {err!r}"
    )
    # The unknown CT is detected pre-network, so no HTTP-shaped error
    # should appear; a connectivity failure would suggest the check
    # was bypassed.
    assert "cannot reach destination" not in err
    assert "TLS verification failed" not in err
