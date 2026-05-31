"""Upsert by NK (FEAT-21a/b).

The upsert routine:

1. Looks up the record by NK on the destination's index.
2. If absent, POSTs the record and inserts the new id into the
   index.
3. If present, compares each field for equality. When the
   destination already matches the snapshot body, the call is a
   noop. Otherwise issue a PATCH with only the differing fields
   so the destination's audit log shows the minimal diff.

Task #28 added a `custom_fields` filter at the write boundary,
the look-ahead path can fire for racks and devices BEFORE the
extras.customfield main phase has imported the field
definitions. Without filtering, NetBox refuses the POST with
HTTP 400 `Custom field 'switch_count' does not exist for this
object type`. The filter consults a lazily-built registry of
which custom fields the destination knows about for each
content type and drops the unknown keys. The main phase for
that content type later PATCHes the values back in once the
field definitions exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.nk_index import NKIndex
from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.resolver import NaturalKey
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS


class UpsertOutcome(Enum):
    """Outcome of a single upsert call, surfaced in the audit log."""

    CREATED = "created"
    UPDATED = "updated"
    NOOP = "noop"
    FAILED = "failed"


@dataclass(frozen=True)
class UpsertResult:
    """Structured result of one upsert."""

    outcome: UpsertOutcome
    content_type: str
    natural_key: NaturalKey
    destination_id: int | None
    message: str = ""


# Task #28: cache of `content_type -> set[custom_field_name]`,
# populated lazily on first call to `_filter_custom_fields`. The
# cache key is the http base URL so a test that exercises two
# destinations does not see cross-contamination.
#
# The value is a nested dict where the OUTER dict is the per-CT
# index and a SPECIAL key `_fetch_failed` (when present) means
# the registry could not be loaded from the destination. In
# that case the filter degrades to "do not filter" so an
# unreachable customfield endpoint cannot cause silent data
# loss on custom_fields.
_KNOWN_CF_CACHE: dict[str, dict[str, set[str]]] = {}
_FETCH_FAILED_SENTINEL = "_fetch_failed"


def _load_destination_customfields(http: NetboxHTTP) -> dict[str, set[str]]:
    """Walk every customfield row on the destination and index
    each by every content type it applies to.

    Uses `?limit=0` so NetBox returns all rows in a single page
    (the standard NetBox convention for "no pagination").
    `http.get_all` follows any `next` links anyway, but we
    pass `limit=0` to avoid a needless second page on the
    common case.

    Both the legacy `object_types: ["dcim.site", ...]` and
    the newer `object_types: [{"value": "dcim.site"}, ...]`
    shapes are accepted defensively.
    """

    by_ct: dict[str, set[str]] = {}
    for row in http.get_all("extras/custom-fields/?limit=0"):
        name = row.get("name")
        if not isinstance(name, str):
            continue
        for ot in row.get("object_types") or []:
            if isinstance(ot, str):
                by_ct.setdefault(ot, set()).add(name)
            elif isinstance(ot, dict):
                value = ot.get("value") or ot.get("name")
                if isinstance(value, str):
                    by_ct.setdefault(value, set()).add(name)
    return by_ct


def _known_custom_fields_for(http: NetboxHTTP, content_type: str) -> set[str] | None:
    """Return the set of custom-field names the destination
    exposes for `content_type`, or None if the registry could
    not be loaded.

    `None` is the "do not filter" signal, the caller treats
    that as "leave custom_fields untouched", which is safer
    than "drop every key" when the destination is
    intermittently unreachable.

    `set()` is "loaded successfully but no CFs for this CT".
    The caller still drops every key in that case, which is
    the correct behaviour, the destination genuinely has no
    custom fields for that content type.
    """

    cache_key = getattr(http, "base_url", "default")
    if cache_key in _KNOWN_CF_CACHE:
        cached = _KNOWN_CF_CACHE[cache_key]
        if _FETCH_FAILED_SENTINEL in cached:
            return None
        return cached.get(content_type, set())

    try:
        by_ct = _load_destination_customfields(http)
    except Exception:  # noqa: BLE001 - degrade to do-not-filter on any error
        # Mark the cache as failed so the filter degrades to
        # do-not-filter rather than retrying the broken fetch
        # on every record.
        _KNOWN_CF_CACHE[cache_key] = {_FETCH_FAILED_SENTINEL: set()}
        return None

    _KNOWN_CF_CACHE[cache_key] = by_ct
    return by_ct.get(content_type, set())


def _filter_custom_fields(
    body: dict[str, Any], http: NetboxHTTP, content_type: str
) -> dict[str, Any]:
    """Drop `body['custom_fields']` keys the destination does
    not yet know about for `content_type`.

    NetBox refuses any POST/PATCH containing an unknown custom
    field. During an import the look-ahead path can fire before
    the extras.customfield phase imports the definitions, so a
    rack POST for example carries `switch_count` in its body
    even though the destination has no such field yet.
    Filtering at the write boundary lets the rest of the body
    through. The main extras.customfield phase imports the
    definitions later; the main dcim.rack phase then PATCHes
    the field values back in via the standard diff path.

    Records whose `custom_fields` dict empties out after the
    filter still get the key in the body, sending `{}` is
    legal for every NetBox version we target.

    If the destination CF registry cannot be loaded
    (`_known_custom_fields_for` returns None), the body is
    passed through untouched. Better to surface the eventual
    HTTP 400 from NetBox than to silently strip all CF data
    when the registry call is broken.

    Returns a NEW body dict; the input is not mutated.
    """

    cf = body.get("custom_fields")
    if not isinstance(cf, dict) or not cf:
        return body
    known = _known_custom_fields_for(http, content_type)
    if known is None:
        # Registry load failed, do not filter, let NetBox tell
        # the operator about any unknown fields directly.
        return body
    filtered = {k: v for k, v in cf.items() if k in known}
    if filtered == cf:
        return body
    out = dict(body)
    out["custom_fields"] = filtered
    return out


def upsert(
    http: NetboxHTTP,
    *,
    content_type: str,
    natural_key: NaturalKey,
    body: Mapping[str, Any],
    index: NKIndex,
    registry: NKRegistry,
) -> UpsertResult:
    """Create-or-update one record on the destination."""

    endpoint = CONTENT_TYPE_ENDPOINTS.get(content_type)
    if endpoint is None:
        return UpsertResult(
            outcome=UpsertOutcome.FAILED,
            content_type=content_type,
            natural_key=natural_key,
            destination_id=None,
            message=f"no endpoint registered for {content_type}",
        )

    index.ensure_built(http, registry, content_type)
    existing_id = index.lookup(content_type, natural_key)
    if existing_id is None:
        # Defensive: collapse enum-dicts at the boundary so a
        # snapshot exported before FEAT-36-blocker (which carries
        # the {value, label} GET shape) can still import. New
        # snapshots after the fix already have flat values; this
        # is a no-op for them.
        # POST/create path: drop None values so fields NetBox
        # refuses with "may not be blank" (e.g. dcim.cable.profile)
        # land at the default instead of failing the whole row.
        # See `_coerce_body_for_write` for the rationale.
        #
        # Task #28: also filter custom_fields keys against the
        # destination's known list so a look-ahead that fires
        # before the extras.customfield phase does not hit a
        # cascade of "Custom field X does not exist" rejections.
        post_body = _filter_custom_fields(
            _coerce_body_for_write(body, drop_nones=True),
            http, content_type,
        )
        try:
            created = http.post(endpoint, post_body)
        except Exception as exc:  # noqa: BLE001 - surface anything to audit
            return UpsertResult(
                outcome=UpsertOutcome.FAILED,
                content_type=content_type,
                natural_key=natural_key,
                destination_id=None,
                message=f"POST failed: {exc!s}",
            )
        new_id = int((created or {}).get("id") or 0) or None
        if new_id is not None:
            index.insert(content_type, natural_key, new_id)
        return UpsertResult(
            outcome=UpsertOutcome.CREATED,
            content_type=content_type,
            natural_key=natural_key,
            destination_id=new_id,
            message="POST 201",
        )

    # Existing record. Decide whether a PATCH is needed.
    detail = http.get_one(f"{endpoint}{existing_id}/") or {}
    diff = {k: v for k, v in body.items() if not _matches(detail.get(k), v)}
    if not diff:
        return UpsertResult(
            outcome=UpsertOutcome.NOOP,
            content_type=content_type,
            natural_key=natural_key,
            destination_id=existing_id,
            message="no diff",
        )
    try:
        # PATCH path: also filter custom_fields against the
        # destination's known list. A diff that contains
        # `custom_fields: {...}` from a pre-customfield-phase
        # POST would otherwise PATCH unknown keys back and
        # trigger the same HTTP 400 the POST filter exists to
        # prevent.
        http.patch(
            f"{endpoint}{existing_id}/",
            _filter_custom_fields(
                _coerce_body_for_write(diff),
                http, content_type,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return UpsertResult(
            outcome=UpsertOutcome.FAILED,
            content_type=content_type,
            natural_key=natural_key,
            destination_id=existing_id,
            message=f"PATCH failed: {exc!s}",
        )
    return UpsertResult(
        outcome=UpsertOutcome.UPDATED,
        content_type=content_type,
        natural_key=natural_key,
        destination_id=existing_id,
        message=f"PATCH on {len(diff)} fields",
    )


def _matches(current: Any, desired: Any) -> bool:
    """Compare a desired snapshot value against the destination's current.

    Special-cases NetBox's nested FK representation: the snapshot
    carries the resolved destination id, while the destination GET
    detail returns the nested object. We compare on `.id` when the
    current side is a dict.
    """
    if isinstance(current, dict) and isinstance(desired, int):
        return bool(current.get("id") == desired)
    return bool(current == desired)


def _coerce_body_for_write(
    body: Mapping[str, Any], *, drop_nones: bool = False
) -> dict[str, Any]:
    """Defensive write-side body coercion.

    Two transforms applied, both safe to run on a body that has
    already been resolved by `_resolve_body`:

    1. **Enum-dict collapse**, the canonical fix lives on the
       export side
       (`nbsnap.export.extractor._collapse_enum_dict`), but we
       coerce here so a legacy snapshot exported before that fix
       can still import via `--allow-enum-dict-bypass`. Fresh
       snapshots are flat already, this is a no-op for them.

    2. **None drop (task #26, opt-in via `drop_nones=True`)**,
       NetBox refuses certain write-only fields with HTTP 400
       `field may not be blank` when the body explicitly carries
       `null`. The canonical case is `dcim.cable.profile`, which
       is documented nullable in the schema but rejected by the
       write validator. Dropping the key entirely tells NetBox
       to use the field's default (which is also null for those
       fields). The caller turns this on for POST/create paths
       only, because a PATCH that legitimately wants to clear a
       field needs the explicit `null` to survive.

       Safety of the broad rule: for every field where NetBox
       DOES accept an explicit `null` on create (most nullable
       FKs and most nullable strings), the field's default is
       also `null`, so omitting the key is semantically the
       same as sending `null` explicitly. The rule only changes
       observable behaviour for fields like `cable.profile`
       where NetBox's write validator disagrees with its
       schema. If a future NetBox version makes a default
       non-null AND requires the field, the import will surface
       that as a regular "required field" error which the
       operator can act on.

    Re-uses the export-side enum helper so the choice-collapse
    rule lives in one place and stays in lockstep.
    """

    from nbsnap.export.extractor import _collapse_enum_dict

    out: dict[str, Any] = {}
    for k, v in body.items():
        coerced = _collapse_enum_dict(v)
        if drop_nones and coerced is None:
            continue
        out[k] = coerced
    return out
