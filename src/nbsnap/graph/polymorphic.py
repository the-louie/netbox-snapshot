"""Polymorphic (generic) FK target discovery (FEAT-05c1/c2/c3).

Generic FKs in NetBox (Cable terminations, IPAddress.assigned_object,
Service.assigned_object, WirelessLink endpoints) point at a tuple
of a content-type id and an object id. The set of legal targets is
not surfaced in the JSON-Schema for the field, but NetBox does
return it inside the `OPTIONS` response for the owning endpoint
(`actions.POST.<field>.choices`).

Per FEAT-05c2, when `OPTIONS` does not surface choices (older NetBox
versions, or a custom plugin field), we fall back to a destination-
only synthetic POST that introspects the validation error to learn
the accepted set. The destination-only constraint is the source
read-only guard rail's contribution to this path.

`FEAT-05c3` is the integration step: build an edge for every
target with `polymorphic_targets` set to the discovered tuple.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nbsnap.graph.model import Edge, Graph, Node
from nbsnap.schema.content_type import ContentType

if TYPE_CHECKING:
    from nbsnap.http.client import NetboxHTTP


def discover_via_options(http: NetboxHTTP, endpoint: str, field: str) -> list[str] | None:
    """Probe the endpoint with `OPTIONS`, return the accepted content types.

    Args:
        http: NetboxHTTP client. OPTIONS is in `READ_ONLY_VERBS`
            so this is safe against the source.
        endpoint: API path of the owning model (e.g. `dcim/cables/`).
        field: The polymorphic field on the model
            (e.g. `a_terminations`).

    Returns:
        A list of `app.model` strings, or `None` when the
        `actions.POST.<field>.choices` path is not present.
    """
    payload = http._request("OPTIONS", endpoint)
    if not isinstance(payload, dict):
        return None
    actions = payload.get("actions") or {}
    post_action = actions.get("POST") or {}
    field_spec = post_action.get(field) or {}
    choices = field_spec.get("choices")
    if not isinstance(choices, list):
        return None
    out: list[str] = []
    for item in choices:
        if isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, str):
                out.append(value)
        elif isinstance(item, str):
            out.append(item)
    return out or None


def discover_via_post_probe(
    http: NetboxHTTP, endpoint: str, field: str
) -> list[str] | None:
    """Destination-only fallback when OPTIONS does not surface choices.

    Sends a deliberately-invalid POST with a sentinel content type
    and parses the validation error message for the list of
    accepted types. NetBox 4.x has historically formatted this
    error as "expected one of [a, b, c]".

    This path is restricted to the destination NetBox; the guard
    rail in `NetboxHTTP` refuses POST against the source.
    """
    if http.is_source():
        # The guard rail would refuse anyway, but exiting early
        # makes the intent visible at the call site.
        return None

    sentinel_payload = {field: {"object_type": "nbsnap.does_not_exist", "object_id": 1}}
    # ARCH-07d: NetboxHTTPError is the domain exception we expect for
    # the deliberately-bad POST below; we read its `body` to learn the
    # valid polymorphic targets NetBox enumerates in the 400 response.
    # Catching it here (not requests.exceptions) keeps this module
    # decoupled from the HTTP transport library.
    from nbsnap.http import NetboxHTTPError

    try:
        http.post(endpoint, sentinel_payload)
    except NetboxHTTPError as exc:
        return _parse_validation_error_for_targets(exc.body, field)
    # An unexpectedly-successful POST is alarming; surface None so
    # the caller falls through to other strategies.
    return None


def _parse_validation_error_for_targets(body: str, _field: str) -> list[str] | None:
    """Look for `app.model` tokens inside a NetBox validation error body."""

    import re

    # NetBox 4.x error shape: '{"a_terminations": {"object_type":
    # ["Invalid content type \\"x\\". Expected one of: dcim.interface,
    # dcim.frontport"]}}'
    matches = re.findall(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", body)
    deduped = sorted(set(matches))
    return deduped or None


def add_polymorphic_edges(
    graph: Graph, owner: str, field: str, targets: list[str], scope: set[str]
) -> None:
    """Wire one edge per discovered target back into the graph.

    The edge's `polymorphic_targets` carries the full discovered
    tuple so the cycle detector knows all the parents that could
    bind the FK, even though each edge nominally has one `parent`.
    """

    in_scope_targets = tuple(t for t in sorted(targets) if t in scope)
    for target in in_scope_targets:
        # The owner must already be a node, but defensively add it
        # in case a caller wires polymorphic discovery before the
        # build pass.
        graph.add_node(Node(owner))
        graph.add_node(Node(target))
        graph.add_edge(
            Edge(
                child=owner,
                parent=target,
                field=field,
                nullable=True,
                required=False,
                is_m2m=False,
                polymorphic_targets=in_scope_targets,
            )
        )


# Hand-curated polymorphic target hints. Each entry records the
# accepted target content types for one polymorphic field on one
# owner. The graph builder uses these to add synthetic ordering
# edges so the planner emits target content types BEFORE the
# owners that reference them, even though the static schema scan
# cannot see those references.
#
# The OPTIONS-based discovery above remains the runtime source of
# truth for `polymorphic_targets`; this table is the cheap upfront
# plan-time hint that keeps the look-ahead resolver from firing in
# the common case.
#
# Each entry carries `verified_against` so a NetBox version bump
# that restructures these fields can be caught by re-running the
# hint check against the new schema.
# ARCH-05c: switched the bare strings to ContentType. Direct
# construction (no from_str) is intentional, several targets sit
# outside the renderer-minimum scope and would otherwise fail
# validation. Callers convert back to strings via ``.as_str()`` where
# the rest of the planner still expects a plain string.
def _ct(raw: str) -> ContentType:
    """Construct a ContentType from a literal app.model string."""

    app, _, model = raw.partition(".")
    return ContentType(app=app, model=model)


POLYMORPHIC_HINTS: list[dict[str, Any]] = [
    {
        "owner_ct": _ct("dcim.cable"),
        "field": "a_terminations",
        "targets": [
            _ct("dcim.interface"), _ct("dcim.frontport"), _ct("dcim.rearport"),
            _ct("dcim.consoleport"), _ct("dcim.consoleserverport"),
            _ct("dcim.powerport"), _ct("dcim.poweroutlet"),
            _ct("circuits.circuittermination"),
        ],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": _ct("dcim.cable"),
        "field": "b_terminations",
        "targets": [
            _ct("dcim.interface"), _ct("dcim.frontport"), _ct("dcim.rearport"),
            _ct("dcim.consoleport"), _ct("dcim.consoleserverport"),
            _ct("dcim.powerport"), _ct("dcim.poweroutlet"),
            _ct("circuits.circuittermination"),
        ],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": _ct("ipam.ipaddress"),
        "field": "assigned_object",
        "targets": [
            _ct("dcim.interface"), _ct("virtualization.vminterface"),
            _ct("ipam.fhrpgroup"),
        ],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": _ct("ipam.service"),
        "field": "parent",
        "targets": [
            _ct("dcim.device"), _ct("virtualization.virtualmachine"),
            _ct("ipam.fhrpgroup"),
        ],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": _ct("wireless.wirelesslink"),
        "field": "interface_a",
        "targets": [_ct("dcim.interface")],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": _ct("wireless.wirelesslink"),
        "field": "interface_b",
        "targets": [_ct("dcim.interface")],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": _ct("dcim.virtualchassis"),
        "field": "master",
        "targets": [_ct("dcim.device")],
        "verified_against": "netbox 4.6.2",
    },
]


# Curated table of FK fields whose validation rules create a
# runtime cycle that the static planner cannot see.
#
# Example, `dcim.device.primary_ip4` looks like a simple FK
# from Device to IPAddress in the OpenAPI schema. The planner
# orders Device after IPAddress and considers the edge done.
# But NetBox's write validator enforces an additional rule,
# the IPAddress's `assigned_object` must point at one of THIS
# device's interfaces. That makes the real dependency graph
# Device -> IPAddress -> Interface -> Device, a multi-hop
# cycle that closes through the Interface relation.
#
# The SCC pass would catch this cycle if Interface.device
# were edge-present at planning time, but Interface is
# created with `device` already set to an int, so the static
# schema does not surface the back-edge. The result is the
# planner leaving these fields off `deferred_edges` and
# Phase-1 trying to POST a Device with primary_ip4 set on a
# fresh destination, which NetBox refuses.
#
# Each entry adds `(content_type, field_name)` to the runtime
# `deferred_fields_by_ct` index so the body resolver strips
# the field before POST and queues a `DeferredFK` for Phase-2
# to PATCH in after the cycle endpoints exist.
#
# Keep this list TIGHT, every entry is a write-time
# constraint NetBox enforces that the static schema does not
# express. Adding an entry that NetBox does not enforce only
# delays the field's value into Phase-2 for no benefit.
KNOWN_VALIDATION_CYCLES: list[dict[str, Any]] = [
    {
        "content_type": _ct("dcim.device"),
        "field": "primary_ip4",
        "note": "IPAddress.assigned_object must be one of device's interfaces",
        "verified_against": "netbox 4.6.2",
    },
    {
        "content_type": _ct("dcim.device"),
        "field": "primary_ip6",
        "note": "same rule as primary_ip4, for IPv6",
        "verified_against": "netbox 4.6.2",
    },
    {
        "content_type": _ct("dcim.device"),
        "field": "oob_ip",
        "note": "same rule as primary_ip4, for out-of-band management",
        "verified_against": "netbox 4.6.2",
    },
    {
        "content_type": _ct("dcim.virtualchassis"),
        "field": "master",
        "note": "Device.virtual_chassis must point back at this chassis",
        "verified_against": "netbox 4.6.2",
    },
]


def known_validation_cycle_fields() -> dict[str, set[str]]:
    """Return the validation-cycle fields as a
    `content_type -> set[field_name]` mapping, ready to merge
    into the driver's `deferred_fields_by_ct` index.

    Mirrors the shape the driver already builds from
    `manifest.deferred_edges`, so the merge is a single
    `setdefault + update` per content type.
    """

    by_ct: dict[str, set[str]] = {}
    for entry in KNOWN_VALIDATION_CYCLES:
        # ARCH-05c: entries now hold ContentType; consumers downstream
        # still index by ``str``, so we round-trip back here. ARCH-05d
        # will widen the consumer to accept ContentType directly.
        ct: str = entry["content_type"].as_str()
        field = entry["field"]
        by_ct.setdefault(ct, set()).add(field)
    return by_ct


def add_hint_edges(graph: Graph, scope: set[str]) -> None:
    """Add synthetic FK edges from the polymorphic hint table.

    For every `(owner, field, target)` triple where both
    endpoints are in scope, add an edge child=owner ->
    parent=target. The edge is marked nullable + m2m so the
    cycle-breaking pass can still defer it if a true cycle
    exists. The label is suffixed with `__hint` so the
    deferred-edge picker can recognise these synthetic edges
    and prefer deferring them over real schema edges.

    Call once per `Graph` build; `Graph.add_edge` appends, it
    does not deduplicate, so repeated calls on the same graph
    would multiply the synthetic edges.
    """

    for hint in POLYMORPHIC_HINTS:
        # ARCH-05c: hint entries hold ContentType; convert to the
        # string shape the rest of the planner (and ``scope``) speaks.
        owner: str = hint["owner_ct"].as_str()
        if owner not in scope:
            continue
        targets_str = [t.as_str() for t in hint["targets"]]
        for target in targets_str:
            if target not in scope:
                continue
            graph.add_node(Node(target))  # idempotent
            graph.add_edge(
                Edge(
                    child=owner,
                    parent=target,
                    field=f"{hint['field']}__hint",
                    nullable=True,
                    required=False,
                    is_m2m=True,
                    polymorphic_targets=tuple(targets_str),
                )
            )
