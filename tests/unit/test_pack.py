"""FEAT-34/35 pack/unpack tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from nbsnap.pack import NBSNAP_EXTENSION, pack, unpack


def test_pack_unpack_round_trip(tmp_path: Path) -> None:
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text('{"version": 1}', encoding="utf-8")
    (snapshot / "dcim").mkdir()
    (snapshot / "dcim" / "sites.jsonl").write_text(
        '{"natural_key": ["hall-d"], "body": {"name": "Hall D"}}\n',
        encoding="utf-8",
    )

    out = tmp_path / f"snap{NBSNAP_EXTENSION}"
    pack(snapshot, out, level=3)
    sidecar = Path(str(out) + ".sha256")
    assert out.exists()
    assert sidecar.exists()

    unpacked = tmp_path / "out"
    unpack(out, unpacked)
    assert (unpacked / "snap" / "manifest.json").exists()
    assert (unpacked / "snap" / "dcim" / "sites.jsonl").exists()


def test_unpack_rejects_unknown_extension(tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-snapshot.tar.zst"
    bogus.write_bytes(b"\x00")
    with pytest.raises(ValueError):
        unpack(bogus, tmp_path / "out")
