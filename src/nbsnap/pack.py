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


class UnsafeTarMemberError(OSError):
    """A tar member would write outside the destination directory.

    Raised by :func:`unpack` before any byte is written when the
    archive contains a member name that resolves outside the operator
    supplied output directory (the classic ``../escape`` pattern) or
    a symlink/hardlink whose target points outside that directory.

    Inherits from :class:`OSError` so callers that already catch the
    broad transport-layer error set (``OSError`` covers most
    filesystem mishaps) keep working without a try/except update.
    """


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


def _refuse_unsafe_members(tf: tarfile.TarFile, out_root: Path) -> None:
    """Reject any tar member whose extraction would escape ``out_root``.

    Three classes of unsafe member are caught:

    1. **Path traversal.** A member whose resolved path is not a
       descendant of ``out_root`` (the classic ``../escape`` or
       absolute ``/etc/passwd`` style). The walk uses
       :meth:`pathlib.Path.resolve` then :meth:`relative_to` so
       symbolic-link components and ``..`` segments cannot trick
       the comparison.
    2. **Escaping symlinks.** A member that is a symlink whose
       ``linkname`` target resolves outside ``out_root``. We refuse
       both absolute and relative targets that point out.
    3. **Escaping hardlinks.** Same check as symlinks; tar's
       hardlink semantics share the ``linkname`` attribute.

    On Python 3.12+ (and 3.11.4+ with the PEP 706 backport)
    :mod:`tarfile` provides the same protections via ``filter="data"``,
    but our runtime floor is 3.11 which predates the backport. Doing
    the check here keeps the security guarantee portable across the
    full supported range and survives a future Python upgrade as a
    redundant belt-and-braces.

    Scope. This prepass is **path-only**. It does not refuse special
    member types (devices, FIFOs, setuid bits) that PEP 706's
    ``filter="data"`` would also strip. nbsnap's own producer
    (:func:`pack`) only emits regular files from a real directory
    tree, so a producer-side compromise is the only threat model
    where those member types could appear, and that is out of scope
    for the SEC-01 hardening. When the Python floor moves to 3.12,
    delete this helper and switch to ``filter="data"``.
    """

    out_resolved = out_root.resolve()
    for member in tf.getmembers():
        candidate = (out_resolved / member.name).resolve()
        try:
            candidate.relative_to(out_resolved)
        except ValueError as exc:
            msg = (
                f"refusing tar member {member.name!r}: "
                f"resolved path {candidate} escapes {out_resolved}"
            )
            raise UnsafeTarMemberError(msg) from exc

        if member.issym() or member.islnk():
            link_target = (candidate.parent / member.linkname).resolve()
            try:
                link_target.relative_to(out_resolved)
            except ValueError as exc:
                msg = (
                    f"refusing tar link {member.name!r} -> {member.linkname!r}: "
                    f"escapes {out_resolved}"
                )
                raise UnsafeTarMemberError(msg) from exc


def unpack(artefact: Path, out: Path) -> Path:
    """Decompress and untar the artefact into `out`, verify sha256.

    Before extraction every member is fed through
    :func:`_refuse_unsafe_members`, which raises
    :class:`UnsafeTarMemberError` if any member would write outside
    ``out``. SEC-01 flagged the previous
    ``tf.extractall(out)`` call as a hardening gap; snapshots can be
    moved between operators by hand, so we cannot assume the tar
    came from a fully trusted producer.
    """

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
        _refuse_unsafe_members(tf, out)
        tf.extractall(out)
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
