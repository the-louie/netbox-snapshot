"""FEAT-05a graph model tests."""

from __future__ import annotations

from nbsnap.graph.model import Edge, Graph, Node


def test_add_node_is_idempotent() -> None:
    g = Graph()
    g.add_node(Node("dcim.site"))
    g.add_node(Node("dcim.site"))
    assert len(g) == 1


def test_add_edge_wires_both_directions() -> None:
    g = Graph()
    g.add_node(Node("dcim.device"))
    g.add_node(Node("dcim.site"))
    g.add_edge(
        Edge(
            child="dcim.device",
            parent="dcim.site",
            field="site",
            nullable=False,
            required=True,
            is_m2m=False,
        )
    )
    assert len(g.out_edges(Node("dcim.device"))) == 1
    assert len(g.in_edges(Node("dcim.site"))) == 1


def test_add_edge_with_missing_endpoint_is_dropped() -> None:
    g = Graph()
    g.add_node(Node("dcim.device"))
    g.add_edge(
        Edge(
            child="dcim.device",
            parent="dcim.site",
            field="site",
            nullable=False,
            required=True,
            is_m2m=False,
        )
    )
    # site never got added, so the edge is dropped.
    assert g.out_edges(Node("dcim.device")) == []


def test_contains_membership() -> None:
    g = Graph()
    g.add_node(Node("ipam.vlan"))
    assert Node("ipam.vlan") in g
    assert Node("ipam.prefix") not in g
