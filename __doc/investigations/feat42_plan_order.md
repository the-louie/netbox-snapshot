# FEAT-42a investigation: why dcim.cable plans before dcim.interface

## Question

Tracing postfix runs shows the planner ordering `dcim.cable` at
position 3 while `dcim.interface` lands at position 17. The
practical effect is that every cable POST triggers a look-ahead
fallback to create its termination interfaces, and ~110
look-aheads then fail because the interface body in the snapshot
references a device that has not been created yet.

## Findings

The order is driven by the SCC planner's deferred-edge picker
prefering the synthetic polymorphic-hint edges over the real
FK edges, which removes `cable -> interface` as a binding
dependency.

Concretely:

1. `POLYMORPHIC_HINTS` in
   `src/nbsnap/graph/polymorphic.py` declares
   `(owner=dcim.cable, field=a_terminations,
   targets=[dcim.interface, ...])` and the matching
   `b_terminations` hint.
2. `add_hint_edges` (same file) materialises these as
   `Edge(child=dcim.cable, parent=dcim.interface,
   field="a_terminations__hint", nullable=True, is_m2m=True,
   polymorphic_targets=...)`.
3. The SCC picker `pick_deferred_edges` in
   `src/nbsnap/graph/algo.py:124` sorts candidates by
   `(not nullable, not m2m, child, field, parent)` and picks the
   first. Hint edges are `nullable=True` AND `is_m2m=True`, so
   they top the priority list every time.
4. With the hint edge deferred, the SCC degenerates and the
   cycle no longer holds cable behind interface. The topological
   sort is then free to position cable wherever its
   non-hint edges allow, which turns out to be position 3
   (after Manufacturer and DeviceType).

The planner code is doing exactly what it was designed to do:
nullable edges are preferred breakers because Phase-2 PATCH can
land them later. The bug is that we WANT cable->interface to
survive the cycle break: the look-ahead recovery costs ~110
extra POSTs per run when interface lands first naturally.

## Reproducer

```python
from nbsnap.export.manifest import Manifest
from nbsnap.graph.algo import plan
from nbsnap.graph.builder import build_graph
from nbsnap.schema.openapi import OpenAPI

snapshot = "/workspace/snapshot-source-frozen"
manifest = Manifest.load(f"{snapshot}/manifest.json")
schema = OpenAPI.load(f"{snapshot}/schema/openapi.json")
graph = build_graph(schema, set(manifest.counts))
p = plan(graph)
print(p.order.index("dcim.cable"), p.order.index("dcim.interface"))
# Today: ('dcim.cable', 3) before ('dcim.interface', 17).
```

## Fix direction

The synthetic hint edges should NOT be the first thing the picker
defers. Several options:

1. **Tag hint edges with a `hint=True` flag and have
   `pick_deferred_edges` sort hints AFTER real edges of the
   same nullable/m2m category.** This is the smallest change and
   keeps the picker's other invariants intact. Implemented in
   FEAT-42b.
2. Add a `KNOWN_DEPENDENCY_HINTS` table that the planner reads
   as non-deferrable edges. More invasive and overlaps with the
   existing hint mechanism.
3. Tighten `POLYMORPHIC_HINTS` to declare specific narrower
   targets (e.g. only `dcim.interface` for cable). This narrows
   the cycle but does not change the picker's preference.

The investigation supports option 1 because the hint edges are
already labelled (`field.endswith("__hint")`), so the picker can
detect them without new data.

## Recommended FEAT-42b implementation

In `pick_deferred_edges`, change the sort key from

```python
key=lambda e: (not e.nullable, not e.is_m2m, e.child, e.field, e.parent)
```

to

```python
key=lambda e: (
    not e.nullable,
    not e.is_m2m,
    e.field.endswith("__hint"),  # hints sort LAST in same tier
    e.child, e.field, e.parent,
)
```

so a real schema edge wins over a hint edge inside the same
nullable/m2m tier. The hint edges remain deferrable as a
last resort, but for the cable/interface cycle the picker now
prefers a real cable->interface edge if one exists.

When NO real edge exists for the same (child, parent) pair, the
hint still gets deferred and behaviour is unchanged.

## Verification

Test (lands in FEAT-42b):

```
tests/unit/test_graph_polymorphic_hints.py::test_cable_orders_after_interface
```

asserts `plan.order.index("dcim.interface") <
plan.order.index("dcim.cable")` against the rescue-10 snapshot's
schema.
