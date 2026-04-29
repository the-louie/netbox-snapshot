"""FEAT-06a/b/c Tarjan + deferred-edge + topo tests."""

from __future__ import annotations

from nbsnap.graph.algo import (
    pick_deferred_edges,
    plan,
    strongly_connected_components,
    topological_order,
)
from nbsnap.graph.model import Edge, Graph, Node


def _edge(child: str, parent: str, *, nullable: bool = False, m2m: bool = False) -> Edge:
    return Edge(
        child=child,
        parent=parent,
        field="link",
        nullable=nullable,
        required=not nullable,
        is_m2m=m2m,
    )


def _graph(nodes: list[str], edges: list[Edge]) -> Graph:
    g = Graph()
    for n in nodes:
        g.add_node(Node(n))
    for e in edges:
        g.add_edge(e)
    return g


def test_scc_on_three_node_cycle() -> None:
    """A 3-node cycle is one SCC of size 3."""

    g = _graph(
        ["A", "B", "C"],
        [_edge("A", "B"), _edge("B", "C"), _edge("C", "A")],
    )
    sccs = strongly_connected_components(g)
    big = [s for s in sccs if len(s) >= 2]
    assert len(big) == 1
    assert {n.content_type for n in big[0]} == {"A", "B", "C"}


def test_scc_on_acyclic_graph_returns_singletons() -> None:
    """No cycle means every SCC has size 1."""

    g = _graph(["A", "B", "C"], [_edge("A", "B"), _edge("B", "C")])
    sccs = strongly_connected_components(g)
    assert all(len(s) == 1 for s in sccs)


def test_pick_deferred_edges_prefers_nullable() -> None:
    """The nullable edge wins over a non-nullable one in a cycle."""

    nullable_edge = _edge("A", "B", nullable=True)
    g = _graph(
        ["A", "B"],
        [nullable_edge, _edge("B", "A")],
    )
    sccs = strongly_connected_components(g)
    scc = next(s for s in sccs if len(s) == 2)
    deferred = pick_deferred_edges(g, scc)
    assert deferred and deferred[0].nullable is True


def test_pick_deferred_edges_returns_empty_when_no_nullable() -> None:
    """A SCC with no nullable/m2m edges yields no deferred candidate."""

    g = _graph(["A", "B"], [_edge("A", "B"), _edge("B", "A")])
    sccs = strongly_connected_components(g)
    scc = next(s for s in sccs if len(s) == 2)
    assert pick_deferred_edges(g, scc) == []


def test_topo_order_respects_dependencies() -> None:
    """Parent comes before child in the topo order."""

    g = _graph(["site", "device"], [_edge("device", "site")])
    order = topological_order(g, deferred=[])
    assert order.index("site") < order.index("device")


def test_plan_breaks_cycle_via_deferred_edge() -> None:
    """End-to-end plan succeeds on a cyclic graph by deferring."""

    g = _graph(
        ["device", "ipaddress"],
        [
            _edge("device", "ipaddress", nullable=True),  # primary_ip4
            _edge("ipaddress", "device"),  # assigned_object_id back to device
        ],
    )
    p = plan(g)
    assert len(p.deferred) == 1
    assert p.deferred[0].nullable is True
    assert set(p.order) == {"device", "ipaddress"}
