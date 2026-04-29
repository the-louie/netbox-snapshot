"""Graph node, edge, and container dataclasses (FEAT-05a).

The graph layer is small on purpose: a few immutable dataclasses
and a `Graph` container with the minimum operations Phase 2 needs.
The actual algorithms (Tarjan, topo sort) live in `algo.py` so the
dataclass module stays focused on shape.

Per RES-04 we do not depend on NetworkX. The container's API is
intentionally smaller than NetworkX's so call sites read as
domain code, not graph-theory code.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Node:
    """A single graph vertex, identified by content type."""

    content_type: str


@dataclass(frozen=True)
class Edge:
    """A directed edge from `child` to `parent` carrying FK metadata.

    The edge direction is "child needs parent to exist first". This
    matches the create-order use case: to insert a Device we need
    the parent Site to exist, so Device's edge points at Site.

    `polymorphic_targets` is non-empty only for generic FK edges
    where the field can point at any of several content types
    (Cable terminations, IPAddress.assigned_object, ...). The
    `parent` field still picks one representative target so the
    edge is well-defined inside the graph.
    """

    child: str
    parent: str
    field: str
    nullable: bool
    required: bool
    is_m2m: bool
    polymorphic_targets: tuple[str, ...] = ()


@dataclass
class Graph:
    """A small directed graph keyed by content type.

    Per RES-04 this is a hand-rolled container, not a NetworkX
    wrapper. The API stays small: add a node, add an edge, list
    edges in/out, iterate the node set, test membership.
    """

    _nodes: set[Node] = field(default_factory=set)
    _out: dict[Node, list[Edge]] = field(default_factory=dict)
    _in: dict[Node, list[Edge]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------
    def add_node(self, node: Node) -> None:
        """Add a node, idempotent on a node that is already present."""
        if node in self._nodes:
            return
        self._nodes.add(node)
        self._out.setdefault(node, [])
        self._in.setdefault(node, [])

    def add_edge(self, edge: Edge) -> None:
        """Add an edge.

        Both endpoints must be nodes already, otherwise the edge is
        dropped silently. The caller (`Graph.from_openapi`) ensures
        nodes are added before edges; dropping the edge if a node
        is missing keeps the container's invariant intact instead
        of carrying broken references.
        """
        child_node = Node(edge.child)
        parent_node = Node(edge.parent)
        if child_node not in self._nodes or parent_node not in self._nodes:
            return
        self._out[child_node].append(edge)
        self._in[parent_node].append(edge)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def out_edges(self, node: Node) -> list[Edge]:
        """Edges pointing **out of** `node` (children -> parents)."""
        return list(self._out.get(node, []))

    def in_edges(self, node: Node) -> list[Edge]:
        """Edges pointing **into** `node`."""
        return list(self._in.get(node, []))

    def nodes(self) -> set[Node]:
        """Return the node set as a snapshot, not the internal mutable one."""
        return set(self._nodes)

    def __contains__(self, node: object) -> bool:
        return isinstance(node, Node) and node in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)
