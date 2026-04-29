"""Build a `Graph` from an `OpenAPI` schema (FEAT-05b).

The builder walks every content type in scope and lands a
child-to-parent edge for each FK field. The result is the input
to the Phase-2 SCC detection (FEAT-06a) and the topo sort
(FEAT-06c).

Out-of-scope content types are dropped from both ends of every
edge: an edge into a non-included content type is silently
ignored so the planner never tries to import something the
operator did not ask for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nbsnap.graph.model import Edge, Graph, Node

if TYPE_CHECKING:
    from nbsnap.schema.openapi import OpenAPI


def from_openapi(openapi: OpenAPI, scope: set[str]) -> Graph:
    """Construct a Graph from the OpenAPI schema, scoped to `scope`.

    Args:
        openapi: The parsed schema; `OpenAPI.iter_endpoints` and
            `OpenAPI.field_spec` are the data sources.
        scope: Content types to include. Both endpoints of any
            edge must be in scope; edges into out-of-scope content
            types are dropped.

    Returns:
        A populated `Graph`. Node iteration order in the output is
        sorted by content type so the resulting graph is
        reproducible.
    """

    graph = Graph()

    # Add every in-scope content type as a node first. This means
    # the edge insertion below can rely on both endpoints existing
    # without doing the membership check inline.
    for content_type in sorted(scope):
        graph.add_node(Node(content_type))

    # Walk endpoints in sorted order so the resulting graph is
    # deterministic across runs.
    for endpoint in sorted(openapi.iter_endpoints(), key=lambda e: e.path):
        ct = endpoint.content_type
        if ct is None or ct not in scope:
            continue

        # The response schema lists the fields we know about.
        # We consult field_spec for each.
        response = openapi._get_response_schema(ct) or {}
        for field_name in sorted((response.get("properties") or {}).keys()):
            spec = openapi.field_spec(ct, field_name)
            if spec.fk_target is None or spec.fk_target not in scope:
                continue
            graph.add_edge(
                Edge(
                    child=ct,
                    parent=spec.fk_target,
                    field=field_name,
                    nullable=spec.nullable,
                    required=spec.required,
                    is_m2m=spec.is_m2m,
                )
            )

    return graph
