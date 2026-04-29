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
    from nbsnap.http.client import NetboxHTTPError

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


def _silence_unused() -> None:  # pragma: no cover, satisfies ruff ARG
    _ = Any
