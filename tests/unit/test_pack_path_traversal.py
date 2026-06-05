"""SEC-01a regression test: path-traversal in ``nbsnap unpack``.

Background
----------
Before SEC-01a, :func:`nbsnap.pack.unpack` extracted with the bare
``TarFile.extractall(out)``. A maliciously crafted snapshot could
therefore contain a member whose name resolved outside ``out`` (e.g.
``../escape``) and Python would happily write to that path.

SEC-01a adds :func:`nbsnap.pack._refuse_unsafe_members`, a portable
prepass that walks every member, resolves its path against the
operator-supplied output directory, and raises
:class:`nbsnap.pack.UnsafeTarMemberError` when a member would escape.
The check runs unconditionally so the security guarantee holds on
the project's full Python floor (3.11, predates the PEP 706
backport in 3.11.4).

What we assert here
-------------------
1. A tar containing ``../escape`` raises ``UnsafeTarMemberError``
   before any byte is written.
2. After the failure, the escape-target file does not exist on disk.

This file deliberately uses the public :func:`unpack` entry point
rather than reaching into :mod:`tarfile` directly. The point is to
lock the behaviour at the caller-visible boundary so future
refactors cannot silently drop the prepass.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest
import zstandard as zstd

from nbsnap.pack import NBSNAP_EXTENSION, UnsafeTarMemberError, unpack


def _build_traversal_artefact(tmp_path: Path) -> Path:
    """Forge a ``.nbsnap.tar.zst`` whose tar contains ``../escape``.

    We bypass :func:`nbsnap.pack.pack` because pack only adds members
    from a real on-disk directory; the malicious member is synthetic
    and must be injected at the tar layer.
    """

    raw_buffer = io.BytesIO()
    with tarfile.open(fileobj=raw_buffer, mode="w") as tf:
        payload = b"pwn"
        info = tarfile.TarInfo(name="../escape")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    raw_bytes = raw_buffer.getvalue()

    artefact = tmp_path / f"malicious{NBSNAP_EXTENSION}"
    artefact.write_bytes(zstd.ZstdCompressor(level=3).compress(raw_bytes))

    sidecar = Path(str(artefact) + ".sha256")
    sidecar.write_text(
        f"{hashlib.sha256(raw_bytes).hexdigest()}  {artefact.name}\n",
        encoding="utf-8",
    )
    return artefact


def test_unpack_refuses_path_traversal_member(tmp_path: Path) -> None:
    artefact = _build_traversal_artefact(tmp_path)
    out_dir = tmp_path / "out"

    with pytest.raises(UnsafeTarMemberError):
        unpack(artefact, out_dir)

    escape_target = tmp_path / "escape"
    assert not escape_target.exists(), (
        "the prepass must refuse the ../escape member before writing"
    )


def _build_symlink_escape_artefact(tmp_path: Path) -> Path:
    """Forge a ``.nbsnap.tar.zst`` whose member is a symlink to ``/etc/passwd``.

    The member name itself (``inside-link``) is safe, but its
    ``linkname`` points at an absolute path outside the destination.
    The symlink branch of :func:`_refuse_unsafe_members` must catch
    this even though the name resolves cleanly.
    """

    raw_buffer = io.BytesIO()
    with tarfile.open(fileobj=raw_buffer, mode="w") as tf:
        info = tarfile.TarInfo(name="inside-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    raw_bytes = raw_buffer.getvalue()

    artefact = tmp_path / f"symlink-escape{NBSNAP_EXTENSION}"
    artefact.write_bytes(zstd.ZstdCompressor(level=3).compress(raw_bytes))
    sidecar = Path(str(artefact) + ".sha256")
    sidecar.write_text(
        f"{hashlib.sha256(raw_bytes).hexdigest()}  {artefact.name}\n",
        encoding="utf-8",
    )
    return artefact


def test_unpack_refuses_symlink_with_absolute_target(tmp_path: Path) -> None:
    artefact = _build_symlink_escape_artefact(tmp_path)
    out_dir = tmp_path / "out"

    with pytest.raises(UnsafeTarMemberError):
        unpack(artefact, out_dir)

    assert not (out_dir / "inside-link").exists(), (
        "the prepass must refuse the absolute-target symlink before writing"
    )


def test_unsafe_tar_member_error_is_oserror() -> None:
    """``UnsafeTarMemberError`` inherits :class:`OSError`.

    Callers that already wrap filesystem operations in ``except OSError``
    keep working without an explicit catch update. This is documented
    in the class docstring; pin it here so a future refactor cannot
    drop the inheritance silently.
    """

    assert issubclass(UnsafeTarMemberError, OSError)
