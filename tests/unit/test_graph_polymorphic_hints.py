"""FEAT-36d tests for the polymorphic-FK ordering hints.

Three behaviours pinned here:

1. With the hint table active, dcim.interface lands before
   dcim.cable in the topo order even though the static schema
   has no edge between them.
2. Hint edges only land when BOTH endpoints are in scope.
3. The synthetic edge carries the `__hint` suffix and the
   `polymorphic_targets` tuple so the deferred-edge picker can
   recognise it.
"""

from __future__ import annotations

from nbsnap.graph import from_openapi, plan
from nbsnap.graph.model import Node
from nbsnap.graph.polymorphic import POLYMORPHIC_HINTS, add_hint_edges
from nbsnap.schema.openapi import OpenAPI


def _minimal_cable_interface_schema() -> dict:
    """Schema with cable and interface, no direct FK between
    them, cable terminations are polymorphic so the static
    scanner cannot see the link."""

    return {
        "paths": {
            "/api/dcim/interfaces/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {
                        "properties": {"id": {}, "name": {}}
                    }}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {
                        "properties": {"name": {}}
                    }}
                }}},
            },
            "/api/dcim/cables/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {
                        "properties": {"id": {}}
                    }}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {"properties": {}}}
                }}},
            },
        }
    }


def test_hints_put_interface_before_cable() -> None:
    """Without the hint table, the static planner would put
    dcim.cable and dcim.interface in alphabetical order (cable
    first). The hint adds a synthetic ordering edge so interface
    lands first."""

    openapi = OpenAPI(_minimal_cable_interface_schema())
    scope = {"dcim.interface", "dcim.cable"}
    graph = from_openapi(openapi, scope=scope)
    p = plan(graph)
    assert p.order.index("dcim.interface") < p.order.index("dcim.cable")


def test_hints_skipped_when_target_out_of_scope() -> None:
    """If the target content type is not in scope, no synthetic
    edge lands. The owner stays at the top of the order with no
    phantom dependency."""

    openapi = OpenAPI(_minimal_cable_interface_schema())
    # Cable in scope, interface NOT in scope.
    graph = from_openapi(openapi, scope={"dcim.cable"})
    out = graph.out_edges(Node("dcim.cable"))
    assert all(e.parent != "dcim.interface" for e in out)


def test_hints_skipped_when_owner_out_of_scope() -> None:
    """If the owner content type is not in scope, no synthetic
    edge lands either."""

    openapi = OpenAPI(_minimal_cable_interface_schema())
    graph = from_openapi(openapi, scope={"dcim.interface"})
    # No edges out of dcim.cable because the node is not in the
    # graph at all.
    assert Node("dcim.cable") not in graph._nodes


def test_hint_edges_carry_field_suffix_and_targets_tuple() -> None:
    """Synthetic edges are tagged with `__hint` so the deferred-
    edge picker can prefer them over real schema edges, and the
    full `polymorphic_targets` tuple is preserved on each."""

    from nbsnap.graph.model import Graph
    graph = Graph()
    for ct in {"dcim.cable", "dcim.interface", "dcim.frontport"}:
        graph.add_node(Node(ct))
    add_hint_edges(graph, scope={"dcim.cable", "dcim.interface", "dcim.frontport"})

    out = graph.out_edges(Node("dcim.cable"))
    # Each of a_terminations/b_terminations becomes one edge per
    # in-scope target; both have the __hint suffix.
    fields = {e.field for e in out}
    assert "a_terminations__hint" in fields
    assert "b_terminations__hint" in fields
    # Every hint edge carries the full target tuple so the
    # cycle-breaking pass can see all candidates.
    for edge in out:
        if edge.field.endswith("__hint"):
            assert edge.polymorphic_targets
            assert "dcim.interface" in edge.polymorphic_targets


def test_hints_marked_nullable_so_cycle_breaker_can_defer() -> None:
    """Hint edges must be nullable + m2m so the SCC-aware
    cycle-breaking pass can defer them when a true cycle exists."""

    from nbsnap.graph.model import Graph
    graph = Graph()
    for ct in {"dcim.cable", "dcim.interface"}:
        graph.add_node(Node(ct))
    add_hint_edges(graph, scope={"dcim.cable", "dcim.interface"})
    for edge in graph.out_edges(Node("dcim.cable")):
        assert edge.nullable is True
        assert edge.is_m2m is True


def test_hints_table_only_references_netbox_4_6_verified_entries() -> None:
    """Every entry in the curated table carries a
    `verified_against` tag. A NetBox version bump that
    restructures these fields should refresh the tag."""

    for hint in POLYMORPHIC_HINTS:
        assert "verified_against" in hint
        assert "netbox" in hint["verified_against"].lower()


def test_cable_orders_after_interface() -> None:
    """FEAT-42b regression: with the picker preferring real
    schema edges over `__hint` edges in the same nullable/m2m
    tier, the planner respects the cable -> interface
    synthetic edge instead of deferring it. Result: interface
    must land at a smaller plan-order index than cable.
    """

    from pathlib import Path

    from nbsnap.graph.algo import plan
    from nbsnap.graph.build import from_openapi as build_graph
    from nbsnap.schema.openapi import OpenAPI

    schema_path = Path("/workspace/snapshot-source-frozen/schema/openapi.json")
    if not schema_path.exists():
        import pytest
        pytest.skip("frozen snapshot schema not available in this sandbox")

    schema = OpenAPI.load(schema_path)
    # Use the renderer-minimum scope so the SCC is non-trivial.
    scope = {
        "dcim.site", "dcim.location", "dcim.rack",
        "dcim.manufacturer", "dcim.devicetype", "dcim.devicerole",
        "dcim.platform", "dcim.device", "dcim.interface",
        "dcim.cable",
        "ipam.vlan", "ipam.prefix", "ipam.ipaddress", "ipam.iprange",
    }
    graph = build_graph(schema, scope)
    p = plan(graph)
    iface_idx = p.order.index("dcim.interface")
    cable_idx = p.order.index("dcim.cable")
    assert iface_idx < cable_idx, (
        f"expected dcim.interface ({iface_idx}) to precede "
        f"dcim.cable ({cable_idx}) in plan order"
    )
