"""ARCH-08a: :func:`relative_path` raises on an unknown content type.

Before ARCH-08a the function silently fell back to
``content_type.replace('.', '/') + '.jsonl'`` so a typo like
``dcim.devic`` ended up writing to ``dcim/devic.jsonl`` instead of
flagging the mistake. This file locks the new hard-failure
behaviour and the carried ``content_type`` attribute on the
exception.
"""

from __future__ import annotations

import pytest

from nbsnap.snapshot.layout import UnknownContentTypeError, relative_path


def test_relative_path_raises_on_unknown_content_type() -> None:
    with pytest.raises(UnknownContentTypeError) as exc:
        relative_path("dcim.devic")  # the classic typo
    assert exc.value.content_type == "dcim.devic"
    # The message also names the offending content type so the operator
    # can grep it out of a log line.
    assert "dcim.devic" in str(exc.value)


def test_unknown_content_type_error_is_a_keyerror() -> None:
    """The exception inherits :class:`KeyError`.

    Any pre-ARCH-08a ``except KeyError`` clause that surfaced the
    silent fallback still catches the new exception during the
    migration window, so callers do not need a coordinated change.
    """

    assert issubclass(UnknownContentTypeError, KeyError)
