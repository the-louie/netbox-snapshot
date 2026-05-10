"""OpenAPI schema fetcher and helpers (FEAT-02a–d).

The OpenAPI document drives every later phase:

* the dependency graph (FEAT-05) needs FK metadata,
* the export writer (FEAT-11) needs the field allowlist,
* the natural-key registry (FEAT-08) cross-references endpoint
  shapes to spot fields that look like natural keys.

This module wraps the parsed schema in a single class with the
helpers each consumer needs. The wrapper hides the OpenAPI dialect
details (`$ref`, request-body schemas under `requestBody.content`,
the difference between `Brief*` and `*Brief` naming) so call sites
read at the level of "what does NetBox say this field accepts".
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nbsnap.http.client import NetboxHTTP

# Snapshot-layout constant, consumed by the writer.
SCHEMA_PATH = "schema/openapi.json"

# Hand-curated content-type table per Q10 burndown. URL -> content type.
# The URL is the slash-stripped, normalised path under /api/.
CURATED_ENDPOINT_CONTENT_TYPES: dict[str, str] = {
    "dcim/device-roles/": "dcim.devicerole",
    "dcim/device-types/": "dcim.devicetype",
    "dcim/virtual-chassis/": "dcim.virtualchassis",
    "dcim/front-ports/": "dcim.frontport",
    "dcim/rear-ports/": "dcim.rearport",
    "dcim/console-ports/": "dcim.consoleport",
    "dcim/console-server-ports/": "dcim.consoleserverport",
    "ipam/prefixes/": "ipam.prefix",
    "ipam/ip-addresses/": "ipam.ipaddress",
    "ipam/ip-ranges/": "ipam.iprange",
    "ipam/asn-ranges/": "ipam.asnrange",
    "ipam/route-targets/": "ipam.routetarget",
    "ipam/fhrp-groups/": "ipam.fhrpgroup",
    "extras/custom-fields/": "extras.customfield",
    "extras/custom-field-choice-sets/": "extras.customfieldchoiceset",
}


@dataclass(frozen=True)
class Operation:
    """One HTTP verb's metadata on an OpenAPI path."""

    method: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class Endpoint:
    """A single OpenAPI path (list or detail) with its operations."""

    path: str
    content_type: str | None
    methods: dict[str, Operation] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldSpec:
    """Shape metadata for a single field on a content type."""

    nullable: bool
    required: bool
    fk_target: str | None
    is_m2m: bool
    write_allowed: bool


def _singularise(plural: str) -> str:
    """Crude English-plural-to-singular for NetBox endpoint names.

    Two flavours of "-es" plural to distinguish:

    1. Stem ends in a sibilant (-ss, -sh, -ch, -x, -zz) so the
       plural is formed by adding "es" wholesale. Singularise by
       stripping "es":
           addresses -> address      (sses)
           prefixes  -> prefix       (xes)
           boxes     -> box          (xes)
           branches  -> branch       (ches)
           dishes    -> dish         (shes)
           buzzes    -> buzz         (zzes)

    2. Stem ends in a silent "e" so the plural is just "+s".
       Singularise by stripping the "s" only:
           leases    -> lease
           ranges    -> range
           services  -> service
           devices   -> device

    Anything that defies the rule lives in
    `CURATED_ENDPOINT_CONTENT_TYPES`.
    """
    word = plural.replace("-", "")
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("sses") or word.endswith(("xes", "ches", "shes", "zzes")):
        return word[:-2]
    if word.endswith("es"):
        return word[:-1]
    if word.endswith("s"):
        return word[:-1]
    return word


def _derive_content_type(path: str) -> str | None:
    """Map an API path to an `app.model` content type.

    Two-layer (per Q10 burndown):
      1. The curated exceptions table wins.
      2. Otherwise apply the URL convention,
         `/api/<app>/<plural-model>/` -> `<app>.<singular-model>`.
    """
    # Strip `/api/` if present, then ensure trailing slash for stable
    # lookups against the curated table.
    cleaned = path.strip("/")
    if cleaned.startswith("api/"):
        cleaned = cleaned[len("api/") :]
    if not cleaned.endswith("/"):
        cleaned = cleaned + "/"

    if cleaned in CURATED_ENDPOINT_CONTENT_TYPES:
        return CURATED_ENDPOINT_CONTENT_TYPES[cleaned]

    parts = cleaned.rstrip("/").split("/")
    # Must look like `<app>/<model>` for the convention to apply.
    # NetBox list endpoints have exactly two segments here; detail
    # endpoints add an `{id}/` we already filter out by checking
    # for the `{` character.
    if len(parts) != 2 or "{" in parts[1]:
        return None
    app, model_plural = parts
    return f"{app}.{_singularise(model_plural)}"


class OpenAPI:
    """Parsed OpenAPI document with NetBox-aware helpers."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        # Caches are filled lazily, the construction cost of a
        # NetBox OpenAPI is tens of megabytes JSON and we want the
        # callers to feel that latency only when they ask.
        self._endpoints_cache: list[Endpoint] | None = None
        self._reverse_index_cache: dict[str, str] | None = None
        self._write_allowlist_cache: dict[str, frozenset[str]] = {}
        self._post_allowlist_cache: dict[str, frozenset[str]] = {}
        self._patch_allowlist_cache: dict[str, frozenset[str]] = {}
        self._read_only_cache: dict[str, frozenset[str]] = {}

    # ------------------------------------------------------------------
    # IO
    # ------------------------------------------------------------------
    @classmethod
    def fetch(cls, http: NetboxHTTP) -> OpenAPI:
        """Fetch `/api/schema/?format=json` via the given client."""
        data = http.get_one("schema/?format=json")
        if not isinstance(data, dict):
            msg = "schema endpoint did not return a JSON object"
            raise RuntimeError(msg)
        return cls(data)

    @classmethod
    def load(cls, path: Path) -> OpenAPI:
        """Load a previously-dumped schema from disk."""
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def dump(self, path: Path) -> None:
        """Write the schema as canonical JSON.

        `sort_keys=True` and `separators=(",", ":")` give us a stable
        byte sequence that we can hash and diff.
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self._canonical_bytes().decode("utf-8"), encoding="utf-8")

    def hash(self) -> str:
        """Return a stable sha256 hex digest of the canonical schema."""
        return hashlib.sha256(self._canonical_bytes()).hexdigest()

    def _canonical_bytes(self) -> bytes:
        return json.dumps(self._raw, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @property
    def raw(self) -> dict[str, Any]:
        """Access the raw schema dict; mostly for tests and debugging."""
        return self._raw

    # ------------------------------------------------------------------
    # Endpoint traversal (FEAT-02b)
    # ------------------------------------------------------------------
    def iter_endpoints(self) -> Iterator[Endpoint]:
        """Yield every `/api/...` endpoint with its operations."""
        if self._endpoints_cache is None:
            self._endpoints_cache = list(self._compute_endpoints())
        yield from self._endpoints_cache

    def _compute_endpoints(self) -> Iterator[Endpoint]:
        paths = self._raw.get("paths") or {}
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            # Only API paths matter.
            if not path.startswith("/api/"):
                continue
            methods: dict[str, Operation] = {}
            for verb_lower, body in path_item.items():
                verb = verb_lower.upper()
                if verb not in {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"}:
                    continue
                if not isinstance(body, dict):
                    continue
                methods[verb] = Operation(method=verb, raw=body)
            yield Endpoint(
                path=path,
                content_type=_derive_content_type(path),
                methods=methods,
            )

    def _resolve_ref(self, ref: str) -> dict[str, Any]:
        """Resolve a `#/components/...` JSON pointer to its schema."""
        if not ref.startswith("#/"):
            msg = f"unsupported $ref form: {ref!r}"
            raise ValueError(msg)
        cursor: Any = self._raw
        for segment in ref[2:].split("/"):
            if not isinstance(cursor, dict):
                msg = f"$ref {ref!r} traversed a non-object node"
                raise ValueError(msg)
            cursor = cursor.get(segment)
            if cursor is None:
                msg = f"$ref {ref!r} not found"
                raise ValueError(msg)
        if not isinstance(cursor, dict):
            msg = f"$ref {ref!r} did not resolve to an object"
            raise ValueError(msg)
        return cursor

    # ------------------------------------------------------------------
    # Field shape (FEAT-02c)
    # ------------------------------------------------------------------
    def _reverse_index(self) -> dict[str, str]:
        """Build `{ModelName: content_type}` by walking endpoints."""
        if self._reverse_index_cache is not None:
            return self._reverse_index_cache

        index: dict[str, str] = {}
        for endpoint in self.iter_endpoints():
            if endpoint.content_type is None:
                continue
            # Model name is the camel case version of the singular
            # form. We avoid heavy NLP, the curated table covers the
            # odd cases and the convention covers the rest.
            _app, model = endpoint.content_type.split(".", 1)
            # Convert to CamelCase: customfieldchoiceset -> CustomFieldChoiceSet.
            # Without lemma data we capitalise word boundaries that come from
            # the curated table or the dash split.
            index[model] = endpoint.content_type
        self._reverse_index_cache = index
        return index

    def _post_request_schema(self, content_type: str) -> dict[str, Any] | None:
        """Return the POST request-body schema for a content type, or None."""
        return self._verb_request_schema(content_type, "POST")

    def _patch_request_schema(self, content_type: str) -> dict[str, Any] | None:
        return self._verb_request_schema(content_type, "PATCH")

    def _get_response_schema(self, content_type: str) -> dict[str, Any] | None:
        """Return the per-record GET response schema for a content type.

        NetBox's list endpoint returns a paginated wrapper
        (``PaginatedXList`` with ``count``, ``next``, ``previous``,
        ``results``); the per-record fields live two levels deep
        inside ``results.items``. We unwrap that here so callers
        get the model schema directly.

        Tries the list endpoint first (always present) and unwraps
        if needed; falls back to the detail endpoint (``{id}/``
        sibling) if the list-side unwrap fails to produce an
        object schema.
        """
        list_schema = self._list_endpoint_schema(content_type)
        if list_schema is not None:
            unwrapped_items = self._unwrap_paginated(list_schema)
            if unwrapped_items is not None:
                # `items` is typically `{"$ref": ".../Device"}`. Resolve
                # so we get the model schema's properties; the field-
                # level `$ref` markers inside that schema survive
                # because `_field_schema` no longer eats them.
                resolved = self._inline_or_ref(unwrapped_items)
                if isinstance(resolved, dict) and "properties" in resolved:
                    return resolved

        # Detail endpoint fallback: scan for a path that shares the
        # same prefix but ends with ``/{id}/``. Detail responses
        # carry the model schema directly, no paginated wrapper.
        detail_schema = self._detail_endpoint_schema(content_type)
        if detail_schema is not None:
            return detail_schema
        return list_schema

    def _list_endpoint_schema(self, content_type: str) -> dict[str, Any] | None:
        for endpoint in self.iter_endpoints():
            if endpoint.content_type != content_type:
                continue
            op = endpoint.methods.get("GET")
            if op is None:
                continue
            responses = op.raw.get("responses") or {}
            two_oh_oh = responses.get("200") or responses.get(200)
            if not isinstance(two_oh_oh, dict):
                continue
            content = (two_oh_oh.get("content") or {}).get("application/json")
            if not isinstance(content, dict):
                continue
            return self._inline_or_ref(content.get("schema") or {})
        return None

    def _detail_endpoint_schema(self, content_type: str) -> dict[str, Any] | None:
        """Hunt for the detail endpoint sibling and return its schema."""
        list_endpoint_path = None
        for endpoint in self.iter_endpoints():
            if endpoint.content_type == content_type:
                list_endpoint_path = endpoint.path
                break
        if list_endpoint_path is None:
            return None
        # Detail path is the list path with `{id}/` appended.
        detail_path = list_endpoint_path.rstrip("/") + "/{id}/"
        for endpoint in self.iter_endpoints():
            if endpoint.path != detail_path:
                continue
            op = endpoint.methods.get("GET")
            if op is None:
                continue
            responses = op.raw.get("responses") or {}
            two_oh_oh = responses.get("200") or responses.get(200)
            if not isinstance(two_oh_oh, dict):
                continue
            content = (two_oh_oh.get("content") or {}).get("application/json")
            if not isinstance(content, dict):
                continue
            return self._inline_or_ref(content.get("schema") or {})
        return None

    @staticmethod
    def _unwrap_paginated(schema: dict[str, Any]) -> dict[str, Any] | None:
        """If `schema` is a `PaginatedXList`, return the inner item schema."""
        if not isinstance(schema, dict):
            return None
        props = schema.get("properties") or {}
        results = props.get("results")
        if not isinstance(results, dict) or results.get("type") != "array":
            return None
        items = results.get("items")
        return items if isinstance(items, dict) else None

    def _verb_request_schema(self, content_type: str, verb: str) -> dict[str, Any] | None:
        for endpoint in self.iter_endpoints():
            if endpoint.content_type != content_type:
                continue
            op = endpoint.methods.get(verb)
            if op is None:
                continue
            body = op.raw.get("requestBody") or {}
            content = (body.get("content") or {}).get("application/json")
            if not isinstance(content, dict):
                continue
            return self._inline_or_ref(content.get("schema") or {})
        return None

    def _inline_or_ref(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Resolve a schema reference into its inline form.

        Handles three cases that NetBox's OpenAPI surface uses:

        1. A direct ``$ref`` to a component schema. The reference is
           resolved and the target schema is returned.
        2. A ``oneOf`` wrapper that lists alternatives (NetBox 4.6+
           uses this for endpoints that accept either a single record
           or an array of records for bulk POST/PATCH). We pick the
           **single-record** branch (the one whose target is an
           object), recurse into it so any inner ``$ref`` is also
           resolved.
        3. A plain inline schema. Returned untouched.

        Without the oneOf handler, the v4.6 allowlist computation
        returns the empty set for every model, which silently strips
        every field from every snapshot row.
        """
        if not isinstance(schema, dict):
            return schema

        ref = schema.get("$ref")
        if isinstance(ref, str):
            return self._inline_or_ref(self._resolve_ref(ref))

        one_of = schema.get("oneOf")
        if isinstance(one_of, list) and one_of:
            for alternative in one_of:
                if not isinstance(alternative, dict):
                    continue
                # Skip the array wrapper, that is the bulk variant.
                # We want the per-record schema.
                if alternative.get("type") == "array":
                    continue
                resolved = self._inline_or_ref(alternative)
                # The resolved branch should look like an object
                # schema with a `properties` dict; if it does not,
                # fall through to the next alternative.
                if isinstance(resolved, dict) and (
                    resolved.get("type") == "object" or "properties" in resolved
                ):
                    return resolved
            # No good alternative found; resolve the first item as
            # a best effort so the caller does not lose information.
            return self._inline_or_ref(one_of[0])

        return schema

    def _field_schema(self, parent: dict[str, Any], field_name: str) -> dict[str, Any] | None:
        """Lookup a field's schema inside a parent object schema.

        Returns the schema **without** resolving any ``$ref`` so that
        the FK-target detector can still see the reference path and
        derive the target content type from the component name.
        Resolving the ``$ref`` here would replace ``{"$ref": "...
        BriefSite"}`` with an anonymous ``{type: object, properties:
        {...}}`` and the target name would be unknowable.
        """
        properties = parent.get("properties") or {}
        sub = properties.get(field_name)
        if not isinstance(sub, dict):
            return None
        return sub

    def field_spec(self, content_type: str, field_name: str) -> FieldSpec:
        """Return the shape metadata for `content_type.field_name`."""

        response_schema = self._get_response_schema(content_type) or {}
        sub = self._field_schema(response_schema, field_name)
        if sub is None:
            # Field missing from the response schema; conservative
            # default: read-only, no FK metadata.
            return FieldSpec(
                nullable=False, required=False, fk_target=None, is_m2m=False, write_allowed=False
            )

        nullable = bool(sub.get("nullable") or False)
        required = field_name in (response_schema.get("required") or [])
        is_m2m = sub.get("type") == "array"
        target_schema = sub.get("items") if is_m2m else sub
        fk_target = self._infer_fk_target(target_schema)

        write_allowed = field_name in self.write_allowlist(content_type)

        return FieldSpec(
            nullable=nullable,
            required=required,
            fk_target=fk_target,
            is_m2m=is_m2m,
            write_allowed=write_allowed,
        )

    def _infer_fk_target(self, schema: Any) -> str | None:
        """FK target detection that handles every NetBox wrapper shape.

        NetBox 4.6 surfaces nested FK fields in three shapes inside
        the response schema:

        1. Direct ``$ref``:  ``{"$ref": ".../BriefSite"}``
        2. ``allOf`` wrapper for nullable FKs:
           ``{"allOf": [{"$ref": "..."}], "nullable": true}``
        3. ``oneOf`` wrapper (rare in response shapes but used in
           request bodies, included here for symmetry):
           ``{"oneOf": [{"$ref": "..."}, ...]}``

        This method walks every wrapper looking for a ``$ref``,
        derives the component name from the path, strips the
        Brief/Nested prefix/suffix, and looks the model up in the
        reverse index built from ``iter_endpoints()``. Falls back to
        a ``title`` lookup on the resolved schema for the rare case
        where the component is inline (NetBox 4.x does this for
        Tag and a few others).
        """
        if not isinstance(schema, dict):
            return None

        candidate_refs = list(self._collect_refs(schema))
        for ref in candidate_refs:
            target_name = ref.rsplit("/", 1)[-1]
            stripped = _strip_brief(target_name)
            mapped = self._reverse_index().get(stripped.lower())
            if mapped is not None:
                return mapped

        # Title-based fallback. If the schema (or any of its
        # wrappers' immediate children) carries a `title` like
        # `BriefIPAddress`, use that as the lookup key.
        for title in self._collect_titles(schema):
            stripped = _strip_brief(title)
            mapped = self._reverse_index().get(stripped.lower())
            if mapped is not None:
                return mapped

        return None

    def _collect_refs(self, schema: dict[str, Any]) -> list[str]:
        """Walk allOf / oneOf wrappers, collect every direct $ref."""
        refs: list[str] = []
        ref = schema.get("$ref")
        if isinstance(ref, str):
            refs.append(ref)
        for key in ("allOf", "oneOf", "anyOf"):
            items = schema.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        refs.extend(self._collect_refs(item))
        return refs

    def _collect_titles(self, schema: dict[str, Any]) -> list[str]:
        """Collect `title` values from this schema and its wrappers."""
        titles: list[str] = []
        title = schema.get("title")
        if isinstance(title, str):
            titles.append(title)
        for key in ("allOf", "oneOf", "anyOf"):
            items = schema.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        titles.extend(self._collect_titles(item))
        return titles

    # ------------------------------------------------------------------
    # Field allowlist (FEAT-02d)
    # ------------------------------------------------------------------
    def write_allowlist(self, content_type: str) -> frozenset[str]:
        cached = self._write_allowlist_cache.get(content_type)
        if cached is not None:
            return cached
        result = self.post_allowlist(content_type) | self.patch_allowlist(content_type)
        self._write_allowlist_cache[content_type] = result
        return result

    def post_allowlist(self, content_type: str) -> frozenset[str]:
        return self._verb_allowlist_cached(content_type, "POST", self._post_allowlist_cache)

    def patch_allowlist(self, content_type: str) -> frozenset[str]:
        return self._verb_allowlist_cached(content_type, "PATCH", self._patch_allowlist_cache)

    def _verb_allowlist_cached(
        self, content_type: str, verb: str, cache: dict[str, frozenset[str]]
    ) -> frozenset[str]:
        cached = cache.get(content_type)
        if cached is not None:
            return cached
        schema = self._verb_request_schema(content_type, verb)
        names: set[str] = set()
        if schema is not None:
            props = schema.get("properties") or {}
            names = set(props.keys())
        result = frozenset(names)
        cache[content_type] = result
        return result

    def read_only_fields(self, content_type: str) -> frozenset[str]:
        cached = self._read_only_cache.get(content_type)
        if cached is not None:
            return cached
        response_schema = self._get_response_schema(content_type) or {}
        response_fields = set((response_schema.get("properties") or {}).keys())
        result = frozenset(response_fields - self.write_allowlist(content_type))
        self._read_only_cache[content_type] = result
        return result

    def dump_allowlists(self, path: Path) -> None:
        """Write per-content-type allowlists to a debug JSON artefact."""
        body: dict[str, dict[str, list[str]]] = {}
        for endpoint in self.iter_endpoints():
            ct = endpoint.content_type
            if ct is None or ct in body:
                continue
            body[ct] = {
                "post": sorted(self.post_allowlist(ct)),
                "patch": sorted(self.patch_allowlist(ct)),
                "read_only": sorted(self.read_only_fields(ct)),
            }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(body, sort_keys=True, indent=2), encoding="utf-8")


_BRIEF_RE = re.compile(r"^(Brief|Nested)|(Brief|Nested)$|Request$|Response$")


def _strip_brief(name: str) -> str:
    """Strip the `Brief*`, `Nested*`, `*Brief`, `*Request` shells used by NetBox."""
    return _BRIEF_RE.sub("", name).strip()
