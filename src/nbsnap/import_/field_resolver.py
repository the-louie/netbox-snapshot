"""Per-field FK resolvers (ARCH-02g scaffold).

The four ``_resolve_*`` helpers that live in
:mod:`nbsnap.import_.driver` (simple FK, polymorphic FK, M2M,
termination-list) all share the same ``(content_type, body, ctx)``
shape ARCH-02b/c introduced. ARCH-02g lifts them into a dedicated
module so ARCH-02h can slim the driver and external callers (the
graph builder, future plugin field rewriters) have a typed import
path to reach for.

This module starts as a re-export point. ARCH-02h moves the actual
function bodies here; until then, the public surface (the four
function names + the typed signature) is what we lock in.
"""

from __future__ import annotations

from nbsnap.import_.driver import (
    _resolve_body,
    _resolve_body_via_ctx,
    _resolve_polymorphic_id_pairs,
    _resolve_termination_lists,
    _safe_resolve_m2m,
)

# Public, typed aliases for the four field-level resolvers. ARCH-02h
# inlines these bodies into this module and drops the legacy
# underscore names.
resolve_body = _resolve_body_via_ctx
resolve_polymorphic_id_pairs = _resolve_polymorphic_id_pairs
resolve_termination_lists = _resolve_termination_lists
safe_resolve_m2m = _safe_resolve_m2m

# Legacy nine-kwarg resolver, kept reachable so ARCH-02h can finish
# the migration without a coordinated big-bang. Not part of the
# documented public surface.
_legacy_resolve_body = _resolve_body


__all__ = [
    "resolve_body",
    "resolve_polymorphic_id_pairs",
    "resolve_safe_m2m",
    "resolve_termination_lists",
    "safe_resolve_m2m",
]

# The audit lists the helper as ``safe_resolve_m2m``; we also expose
# the snake-case ``resolve_safe_m2m`` for consistency with the other
# three names that start with ``resolve_``. Both point at the same
# function so a future caller can pick whichever reads best.
resolve_safe_m2m = safe_resolve_m2m
