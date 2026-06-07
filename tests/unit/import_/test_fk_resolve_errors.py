"""ARCH-09b: :class:`ResolverFKMissError` contract tests.

Mirror of ``tests/unit/natkey/test_resolver_errors.py`` for the
FK-side error type. We pin four attributes plus the render shape so
audit-row consumers can rely on the format.
"""

from __future__ import annotations

from nbsnap.import_.fk_resolve import ResolverFKMissError


def test_fk_miss_error_attributes_round_trip() -> None:
    err = ResolverFKMissError(
        "NK ('1.2.3.4/32',) not found on destination",
        content_type="dcim.device",
        natural_key=(("c",), "d39a"),
        target_ct="ipam.ipaddress",
        hint="missing source data",
    )

    assert err.content_type == "dcim.device"
    assert err.natural_key == (("c",), "d39a")
    assert err.target_ct == "ipam.ipaddress"
    assert err.hint == "missing source data"


def test_fk_miss_error_renders_single_line() -> None:
    """``str()`` formats as ``[child nk -> target] message (hint: hint)``."""

    err = ResolverFKMissError(
        "NK not found",
        content_type="dcim.interface",
        natural_key=(("c",), "C-ESPORTS-CITY-2-SW", "ge-0/0/8"),
        target_ct="dcim.cable",
        hint="schema skew",
    )

    rendered = str(err)
    assert rendered.startswith("[dcim.interface ")
    assert " -> dcim.cable]" in rendered
    assert "NK not found" in rendered
    assert "schema skew" in rendered


def test_fk_miss_error_inherits_key_error() -> None:
    """Legacy ``except KeyError`` clauses still fire during migration."""

    assert issubclass(ResolverFKMissError, KeyError)
