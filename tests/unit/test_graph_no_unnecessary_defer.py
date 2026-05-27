"""FEAT-36g regression tests: only defer edges in real cycles.

The planner's deferred-edge picker must not defer edges that
sit on the DAG between distinct SCCs. Deferring a nullable
acyclic edge wastes a Phase-2 PATCH and ships a row with the FK
unset until Phase-2 catches up.

Three behaviours pinned here:

1. A nullable edge with no return path lands as a normal
   DAG edge; the planner orders the parent first.
2. A self-loop still defers normally, that is the only legal
   defer in a size-1 SCC.
3. A real two-node cycle defers exactly one (the nullable side).
"""

from __future__ import annotations

from nbsnap.graph.algo import (
    pick_deferred_edges,
    plan,
    strongly_connected_components,
)
from nbsnap.graph.model import Edge, Graph, Node


def _edge(child: str, parent: str, *, nullable: bool = False, m2m: bool = False) -> Edge:
    return Edge(
        child=child, parent=parent, field="x",
        nullable=nullable, required=not nullable, is_m2m=m2m,
    )


def _graph_with(edges: list[Edge]) -> Graph:
    g = Graph()
    for e in edges:
        g.add_node(Node(e.child))
        g.add_node(Node(e.parent))
    for e in edges:
        g.add_edge(e)
    return g


def test_acyclic_nullable_edge_is_not_deferred() -> None:
    """A nullable edge A.b -> B with no return edge sits on the
    DAG, not deferred. B lands before A in the topo order."""

    g = _graph_with([_edge("ipam.prefix", "ipam.vlan", nullable=True)])
    p = plan(g)
    assert p.deferred == []
    assert p.order.index("ipam.vlan") < p.order.index("ipam.prefix")


def test_self_loop_is_still_deferred() -> None:
    """Self-loops (role.parent -> role) keep their deferred
    behaviour, the only way to break the loop."""

    g = _graph_with([_edge("dcim.devicerole", "dcim.devicerole", nullable=True)])
    p = plan(g)
    assert len(p.deferred) == 1
    assert p.deferred[0].child == "dcim.devicerole"
    assert p.deferred[0].parent == "dcim.devicerole"


def test_two_node_cycle_defers_one_edge() -> None:
    """Real cycle A <-> B: pick_deferred_edges picks the
    nullable side and the planner emits a topo order without
    it."""

    g = _graph_with([
        _edge("A", "B", nullable=True),
        _edge("B", "A"),
    ])
    sccs = strongly_connected_components(g)
    cycle_scc = next(s for s in sccs if len(s) == 2)
    deferred = pick_deferred_edges(g, cycle_scc)
    assert len(deferred) == 1
    assert deferred[0].child == "A"  # nullable side wins


def test_three_node_dag_with_nullable_middle_edge() -> None:
    """A -> B (nullable) -> C is a pure DAG; no defers happen
    even though the middle edge is nullable."""

    g = _graph_with([
        _edge("A", "B", nullable=True),
        _edge("B", "C"),
    ])
    p = plan(g)
    assert p.deferred == []
    assert p.order.index("C") < p.order.index("B") < p.order.index("A")


def test_nullable_edge_into_separate_scc_is_not_deferred() -> None:
    """If A->B is a nullable edge AND B is in a separate SCC
    (B is its own size-1 SCC with no return path), the edge is
    NOT deferred: it does not close a cycle."""

    g = _graph_with([_edge("A", "B", nullable=True)])
    sccs = strongly_connected_components(g)
    a_scc = next(s for s in sccs if Node("A") in s)
    deferred = pick_deferred_edges(g, a_scc)
    # A is in its own size-1 SCC with no self-edge; nothing to
    # defer.
    assert deferred == []


def test_self_edge_within_larger_scc_still_eligible() -> None:
    """If A.parent -> A is a self-edge AND A is also in a true
    cycle with B, both kinds of back-edge are deferral
    candidates. The picker chooses the cheapest."""

    g = _graph_with([
        _edge("A", "A", nullable=True),  # self-loop
        _edge("A", "B", nullable=True),
        _edge("B", "A"),
    ])
    p = plan(g)
    # At minimum the self-loop OR a cycle-closer is deferred so
    # the topo sort can succeed; if no defer happened, the order
    # would not produce A and B at all.
    assert len(p.deferred) >= 1
    assert "A" in p.order
    assert "B" in p.order
