"""`nbsnap pack` and `nbsnap unpack` (FEAT-34 / FEAT-35).

Packs a snapshot directory into a single `.nbsnap.tar.zst` artefact
and writes a sidecar `.sha256` file with the integrity hash per
RES-03. Unpack reverses the operation, verifying the hash before
extracting.

zstd-level 19 is the default per the RES-03 trade-off; operators
can pass `--level N`.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tarfile
from pathlib import Path

import zstandard as zstd

NBSNAP_EXTENSION = ".nbsnap.tar.zst"
DEFAULT_LEVEL = 19


def add_pack_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("snapshot_dir", type=Path, help="snapshot directory to pack")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"output filename (default <name>{NBSNAP_EXTENSION})",
    )
    parser.add_argument(
        "--level",
        type=int,
        default=DEFAULT_LEVEL,
        help=f"zstd compression level (default {DEFAULT_LEVEL})",
    )


def add_unpack_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artefact", type=Path, help="<name>.nbsnap.tar.zst input")
    parser.add_argument("--out", type=Path, required=True, help="output directory")


def pack(snapshot_dir: Path, out: Path, level: int = DEFAULT_LEVEL) -> Path:
    """Tar+zstd the snapshot directory, write sha256 sidecar."""

    snapshot_dir = Path(snapshot_dir)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tf:
        tf.add(snapshot_dir, arcname=snapshot_dir.name)
    raw = buffer.getvalue()

    digest = hashlib.sha256(raw).hexdigest()
    cctx = zstd.ZstdCompressor(level=level)
    compressed = cctx.compress(raw)
    out.write_bytes(compressed)
    Path(str(out) + ".sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    return out


def unpack(artefact: Path, out: Path) -> Path:
    """Decompress and untar the artefact into `out`, verify sha256."""

    artefact = Path(artefact)
    out = Path(out)
    if not str(artefact).endswith(NBSNAP_EXTENSION):
        msg = f"refusing to unpack non-{NBSNAP_EXTENSION} file"
        raise ValueError(msg)
    compressed = artefact.read_bytes()
    dctx = zstd.ZstdDecompressor()
    raw = dctx.decompress(compressed)

    sidecar = Path(str(artefact) + ".sha256")
    if sidecar.exists():
        expected = sidecar.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(raw).hexdigest()
        if expected != actual:
            msg = f"sha256 mismatch, expected {expected}, got {actual}"
            raise RuntimeError(msg)

    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tf:
        tf.extractall(out)  # noqa: S202 - paths come from trusted operator
    return out


def run_pack(args: argparse.Namespace) -> int:
    out = args.out or (args.snapshot_dir.parent / (args.snapshot_dir.name + NBSNAP_EXTENSION))
    target = pack(args.snapshot_dir, out, level=args.level)
    sys.stderr.write(f"# packed to {target}\n")
    return 0


def run_unpack(args: argparse.Namespace) -> int:
    target = unpack(args.artefact, args.out)
    sys.stderr.write(f"# unpacked to {target}\n")
    return 0
