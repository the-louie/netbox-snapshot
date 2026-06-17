"""ARCH-05c: polymorphic constants store ``ContentType``, not bare strings.

A regression test: a future contributor adding an entry that uses a
plain string would silently bypass the typed access pattern and
trip the runtime ``as_str()`` calls in the consumers. Pin the type
of every entry here.
"""

from __future__ import annotations

from nbsnap.graph.polymorphic import KNOWN_VALIDATION_CYCLES, POLYMORPHIC_HINTS
from nbsnap.schema.content_type import ContentType


def test_polymorphic_hints_owner_ct_is_content_type() -> None:
    for hint in POLYMORPHIC_HINTS:
        assert isinstance(hint["owner_ct"], ContentType), (
            f"hint {hint!r} has non-ContentType owner_ct"
        )


def test_polymorphic_hints_targets_are_content_types() -> None:
    for hint in POLYMORPHIC_HINTS:
        for target in hint["targets"]:
            assert isinstance(target, ContentType), (
                f"hint {hint!r} has non-ContentType target {target!r}"
            )


def test_known_validation_cycles_content_type_is_content_type() -> None:
    for entry in KNOWN_VALIDATION_CYCLES:
        assert isinstance(entry["content_type"], ContentType), (
            f"entry {entry!r} has non-ContentType content_type"
        )
