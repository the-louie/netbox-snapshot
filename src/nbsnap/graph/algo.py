"""Tarjan SCC + deferred-edge picker + topo sort (FEAT-06a/b/c).

The graph has direction child -> parent. The plan we want is the
*reverse* of a topological sort over that graph: parents first,
then children, so a child can always reference its parent's
already-created id.

Cycles (Device.primary_ip4 -> IPAddress -> Interface -> Device) are
broken by deferring one nullable edge per cycle to a Phase-2
post-pass. The Phase-2 pass issues a PATCH against the already-
created child to set the FK once the parent exists.

The hand-rolled Tarjan is the one decided in RES-04.
"""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import TopologicalSorter

from nbsnap.graph.model import Edge, Graph, Node


@dataclass(frozen=True)
class Plan:
    """Output of the planner.

    `order` is the import order: a flat list of content types,
    parents before children. `deferred` carries the edges the
    planner broke to make the graph acyclic; the importer's
    Phase-2 pass walks these to issue the PATCH calls.
    """

    order: list[str]
    deferred: list[Edge]


# ---------------------------------------------------------------------------
# Tarjan SCC (FEAT-06a)
# ---------------------------------------------------------------------------


def strongly_connected_components(graph: Graph) -> list[list[Node]]:
    """Return the SCCs of `graph` in reverse-topological order.

    Implementation is the classic Tarjan algorithm with an explicit
    work stack instead of recursion so the import graph can grow
    without tripping Python's recursion limit.

    Children of a node are visited in sorted order so the output
    is deterministic across runs, which matters for the human-
    readable plan diff in FEAT-07b.
    """

    indexes: dict[Node, int] = {}
    lowlinks: dict[Node, int] = {}
    on_stack: set[Node] = set()
    stack: list[Node] = []
    sccs: list[list[Node]] = []
    next_index = 0

    def successors(node: Node) -> list[Node]:
        # Edge direction is child -> parent; SCCs follow that direction.
        return sorted(
            {Node(edge.parent) for edge in graph.out_edges(node)},
            key=lambda n: n.content_type,
        )

    for root in sorted(graph.nodes(), key=lambda n: n.content_type):
        if root in indexes:
            continue
        # Each entry on the work stack is (node, iterator over its
        # successors). We push when we descend, pop when we finish
        # all successors.
        work: list[tuple[Node, list[Node], int]] = []
        indexes[root] = next_index
        lowlinks[root] = next_index
        next_index += 1
        stack.append(root)
        on_stack.add(root)
        work.append((root, successors(root), 0))

        while work:
            node, succs, cursor = work[-1]
            if cursor < len(succs):
                # Advance the cursor on this frame so we know which
                # successor to continue from next time.
                work[-1] = (node, succs, cursor + 1)
                child = succs[cursor]
                if child not in indexes:
                    indexes[child] = next_index
                    lowlinks[child] = next_index
                    next_index += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, successors(child), 0))
                elif child in on_stack:
                    lowlinks[node] = min(lowlinks[node], indexes[child])
                continue

            # Finished with this node's successors.
            if lowlinks[node] == indexes[node]:
                scc: list[Node] = []
                while True:
                    popped = stack.pop()
                    on_stack.discard(popped)
                    scc.append(popped)
                    if popped == node:
                        break
                sccs.append(scc)
            work.pop()
            if work:
                parent_node = work[-1][0]
                lowlinks[parent_node] = min(lowlinks[parent_node], lowlinks[node])

    return sccs


# ---------------------------------------------------------------------------
# Deferred-edge selection (FEAT-06b)
# ---------------------------------------------------------------------------


def pick_deferred_edges(graph: Graph, scc: list[Node]) -> list[Edge]:
    """Pick at most one edge per SCC to defer to Phase 2.

    Selection rule:
      1. Prefer a `nullable=True` edge, the FK can be left blank at
         create time.
      2. Prefer an edge from an `is_m2m` field, m2m edges can be
         set post-create with a separate call.
      3. Prefer the alphabetically-earliest content type, just so
         the choice is deterministic.

    A SCC with no nullable edges is reported back via the empty
    list; the caller is expected to surface that as a friction
    requiring human input.
    """
    if len(scc) <= 1:
        # Self-loop on a single node. Defer *every* eligible
        # self-edge, not just the first; NetBox models often have
        # more than one self-edge (IPAddress has `nat_inside` and
        # `nat_outside`, Prefix has `parent`, etc.) and leaving any
        # of them behind would re-introduce the cycle the size-1
        # SCC was supposed to break.
        deferred: list[Edge] = []
        for node in scc:
            for edge in graph.out_edges(node):
                if Node(edge.parent) in scc and (edge.nullable or edge.is_m2m):
                    deferred.append(edge)
        return deferred

    candidates: list[Edge] = []
    scc_set = set(scc)
    for node in sorted(scc, key=lambda n: n.content_type):
        for edge in graph.out_edges(node):
            if Node(edge.parent) not in scc_set:
                continue
            if edge.nullable or edge.is_m2m:
                candidates.append(edge)

    if not candidates:
        return []
    # Stable sort by (nullable, m2m, alphabetical) so the choice
    # does not flip across runs.
    candidates.sort(
        key=lambda e: (not e.nullable, not e.is_m2m, e.child, e.field, e.parent)
    )
    return [candidates[0]]


# ---------------------------------------------------------------------------
# Final topo sort (FEAT-06c)
# ---------------------------------------------------------------------------


def topological_order(graph: Graph, deferred: list[Edge]) -> list[str]:
    """Topologically sort the graph with `deferred` edges removed.

    The output is the create-order list of content types: parents
    first, then children. Uses `graphlib.TopologicalSorter` from
    the stdlib; we removed the deferred edges already so the
    sorter sees a DAG.

    Raises `ValueError` (graphlib's `CycleError` subclass) if the
    graph still contains a cycle after deferred-edge removal.
    """

    deferred_keys = {(e.child, e.parent, e.field) for e in deferred}

    ts: TopologicalSorter[str] = TopologicalSorter()
    for node in graph.nodes():
        ts.add(node.content_type)
        for edge in graph.out_edges(node):
            key = (edge.child, edge.parent, edge.field)
            if key in deferred_keys:
                continue
            # Parent must be created before child. The sorter takes
            # "node depends on these other nodes", so child depends
            # on parent.
            ts.add(edge.child, edge.parent)

    return list(ts.static_order())


# ---------------------------------------------------------------------------
# End-to-end planner
# ---------------------------------------------------------------------------


def plan(graph: Graph) -> Plan:
    """End-to-end planning, detect SCCs, defer cycle-closers, sort.

    The Tarjan pass + per-SCC deferred-edge selection handles every
    "well-shaped" cycle: a clean back-edge per SCC member. NetBox
    has a few asymmetric cases (a node with two self-edges,
    overlapping cycles across SCC boundaries when scope is narrow)
    where one defer-pass leaves residual cycles.

    To make the planner robust against those edge cases we wrap the
    final topological sort in a defer-and-retry loop: any
    `CycleError` from the sort surfaces the offending node tuple,
    we then add the first eligible back-edge between those nodes
    to the deferred list and retry. The loop has a hard cap so a
    truly broken graph fails loudly instead of hanging.
    """

    from graphlib import CycleError

    sccs = strongly_connected_components(graph)
    deferred: list[Edge] = []
    for scc in sccs:
        if len(scc) == 1:
            node = scc[0]
            self_loop = any(Node(e.parent) == node for e in graph.out_edges(node))
            if not self_loop:
                continue
        deferred.extend(pick_deferred_edges(graph, scc))

    # Defer-and-retry safety net. A NetBox graph has under a hundred
    # cycles even at the largest scope, so a cap of 50 retries is
    # generous; in practice this loop fires at most once or twice.
    for _attempt in range(50):
        try:
            order = topological_order(graph, deferred)
            return Plan(order=order, deferred=deferred)
        except CycleError as exc:
            cycle_nodes = [str(n) for n in exc.args[1] if isinstance(n, str)]
            extra = _pick_one_back_edge_in_cycle(graph, cycle_nodes, deferred)
            if extra is None:
                # No nullable/m2m edge available to break; surface
                # the error so the operator sees it.
                raise
            deferred.append(extra)

    # 50 retries without convergence means something is deeply
    # wrong; let the last CycleError propagate.
    return Plan(order=topological_order(graph, deferred), deferred=deferred)


def _pick_one_back_edge_in_cycle(
    graph: Graph, cycle_nodes: list[str], already_deferred: list[Edge]
) -> Edge | None:
    """Find a nullable/m2m edge between any pair of nodes in the cycle."""

    deferred_keys = {(e.child, e.parent, e.field) for e in already_deferred}
    cycle_set = set(cycle_nodes)
    for node_name in sorted(cycle_set):
        for edge in graph.out_edges(Node(node_name)):
            if edge.parent not in cycle_set:
                continue
            key = (edge.child, edge.parent, edge.field)
            if key in deferred_keys:
                continue
            if edge.nullable or edge.is_m2m:
                return edge
    return None
