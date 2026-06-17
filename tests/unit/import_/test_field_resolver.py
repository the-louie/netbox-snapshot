"""ARCH-02g: ``field_resolver`` re-exports the four resolvers.

We pin object-identity equality between the re-exports and the
legacy underscore names so a refactor that swaps one of the helpers
for a copy fails here loudly.
"""

from __future__ import annotations


def test_resolve_body_is_via_ctx_wrapper() -> None:
    from nbsnap.import_.driver import _resolve_body_via_ctx
    from nbsnap.import_.field_resolver import resolve_body

    assert resolve_body is _resolve_body_via_ctx


def test_resolve_polymorphic_id_pairs_identity() -> None:
    from nbsnap.import_.driver import _resolve_polymorphic_id_pairs
    from nbsnap.import_.field_resolver import resolve_polymorphic_id_pairs

    assert resolve_polymorphic_id_pairs is _resolve_polymorphic_id_pairs


def test_safe_resolve_m2m_identity() -> None:
    from nbsnap.import_.driver import _safe_resolve_m2m
    from nbsnap.import_.field_resolver import resolve_safe_m2m, safe_resolve_m2m

    assert safe_resolve_m2m is _safe_resolve_m2m
    assert resolve_safe_m2m is safe_resolve_m2m


def test_resolve_termination_lists_identity() -> None:
    from nbsnap.import_.driver import _resolve_termination_lists
    from nbsnap.import_.field_resolver import resolve_termination_lists

    assert resolve_termination_lists is _resolve_termination_lists
