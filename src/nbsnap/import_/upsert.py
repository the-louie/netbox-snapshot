"""Upsert by NK (FEAT-21a/b).

The upsert routine:

1. Looks up the record by NK on the destination's index.
2. If absent, POSTs the record and inserts the new id into the
   index.
3. If present, compares each field for equality. When the
   destination already matches the snapshot body, the call is a
   noop. Otherwise issue a PATCH with only the differing fields
   so the destination's audit log shows the minimal diff.

A `custom_fields` filter runs at the write boundary because
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
    # The record body is structurally incomplete after
    # resolution (e.g. a dcim.cable with neither
    # a_terminations nor b_terminations) and posting it would
    # produce a confusing NetBox `__all__` error. The upsert
    # path short-circuits these rows so the operator sees a
    # clean `skipped: N` in the summary instead of an opaque
    # validation failure burying the real issues.
    SKIPPED = "skipped"


@dataclass(frozen=True)
class UpsertResult:
    """Structured result of one upsert."""

    outcome: UpsertOutcome
    content_type: str
    natural_key: NaturalKey
    destination_id: int | None
    message: str = ""


# Cache of `content_type -> set[custom_field_name]`,
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

    if getattr(http, "_cf_cache_failed", False):
        return None
    # BUG-03: before the customfield phase finishes, an empty
    # destination registry is the expected state, not a signal
    # to strip every key. Return None (do not filter) so the
    # look-ahead path does not silently nuke CF values that the
    # main phase will land moments later.
    if not getattr(http, "_cf_phase_complete", False):
        return None
    cached = getattr(http, "_cf_cache", None)
    if cached is not None:
        return cached.get(content_type, set())

    try:
        by_ct = _load_destination_customfields(http)
    except Exception:  # noqa: BLE001 - degrade to do-not-filter on any error
        # Mark the instance cache as failed so the filter
        # degrades to do-not-filter rather than retrying the
        # broken fetch on every record.
        if hasattr(http, "_cf_cache_failed"):
            http._cf_cache_failed = True
        return None

    if hasattr(http, "_cf_cache"):
        http._cf_cache = by_ct
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


# Curated table of NetBox POST failures that reflect
# destination policy rather than tool bugs, and should surface
# as SKIPPED rather than FAILED in the audit summary. Each
# entry pairs a content type with a substring fragment of the
# error body and a human-readable explanation that the audit
# log carries on the result.
#
# Why match by substring? NetBox's HTTP 400 bodies are not
# stable JSON shapes across versions, the error text inside
# the `__all__` or named-field arrays IS stable for the cases
# we know about. A loose substring match is the right
# robustness trade-off here.
#
# Add entries sparingly, every entry hides a class of failure
# from the operator's primary error count, so the explanation
# field should clearly tell them what to investigate.
import re

# BUG-05: structural matchers tolerate cosmetic NetBox error
# rewording (e.g. "addresses overlap with range" -> "addresses
# overlap with the range") because each pattern is a regex.
# The `keywords` list backs the "near-miss" detector: when an
# error text contains the keywords but does NOT match the
# regex, we log at INFO so a maintainer notices NetBox drifted.
# `verified_against` documents the last NetBox release the
# regex was confirmed against; refresh by running the rescue
# loop against a newer NetBox and updating either the version
# tag or the regex (whichever drifted).
_POST_FAILURE_SKIP_PATTERNS: list[dict[str, Any]] = [
    {
        "content_type": "ipam.iprange",
        "regex": re.compile(
            r"addresses\s+overlap\s+with\s+(?:the\s+)?range",
            re.IGNORECASE,
        ),
        "keywords": ("addresses", "overlap", "range"),
        "verified_against": "NetBox 4.6.2",
        "explanation": (
            "iprange refused due to overlap with an existing range. "
            "The source NetBox allowed this overlap; the destination's "
            "ENFORCE_GLOBAL_UNIQUE policy refuses it. Either relax the "
            "destination policy or remove the overlapping snapshot row."
        ),
    },
    {
        "content_type": "ipam.ipaddress",
        "regex": re.compile(
            r"duplicate\s+IP\s+(?:address\s+)?(?:found|detected)",
            re.IGNORECASE,
        ),
        "keywords": ("duplicate", "IP"),
        "verified_against": "NetBox 4.6.2",
        "explanation": (
            "ip-address refused due to a duplicate already on the "
            "destination. The source NetBox allowed this duplicate; the "
            "destination's ENFORCE_GLOBAL_UNIQUE policy refuses it. "
            "Either relax the destination policy or de-duplicate the "
            "source data."
        ),
    },
]


def _classify_post_failure(content_type: str, error_text: str) -> str | None:
    """Inspect a failed-POST error body and decide whether the
    failure is one of the known skip-rather-than-fail cases.

    Returns the explanation string from
    `_POST_FAILURE_SKIP_PATTERNS` when a pattern matches, or
    None to leave the result as a regular FAILED outcome.

    BUG-05: the matchers are now regex shapes rather than
    fixed-string substrings, so a cosmetic NetBox reword does
    not silently flip the row from SKIPPED to FAILED. The
    "near-miss" branch logs at INFO when the error text
    contains all the pattern keywords but fails the regex, so
    a maintainer sees the drift and can refresh the pattern.
    """

    import logging
    log = logging.getLogger(__name__)

    for entry in _POST_FAILURE_SKIP_PATTERNS:
        if entry["content_type"] != content_type:
            continue
        if entry["regex"].search(error_text):
            return entry["explanation"]
        if all(kw.lower() in error_text.lower() for kw in entry["keywords"]):
            log.info(
                "BUG-05 near miss: %s error contains all pattern "
                "keywords but did not match the structural regex "
                "(verified_against=%s). This may indicate NetBox "
                "reworded the error; consider refreshing the regex.",
                content_type, entry["verified_against"],
            )
    return None


def _record_is_structurally_incomplete(
    content_type: str, body: Mapping[str, Any]
) -> str | None:
    """Decide whether `body` is missing required structure for
    `content_type`. Returns a human-readable reason string when
    the record should be skipped, or None to proceed.

    Today this is wired only for `dcim.cable`, where NetBox
    requires BOTH `a_terminations` and `b_terminations` to be
    non-empty arrays. Without that, the POST returns the
    aggregate `__all__: Must define A and B terminations`
    error which is hard to act on. Skipping the row instead
    surfaces a clean audit entry and lets the rest of the
    import continue without the misleading failure noise.

    Other content types with similar required-structure rules
    can be added here as they surface; keep the checks tight
    to known cases so we never skip a legitimately-empty
    optional record.
    """

    if content_type != "dcim.cable":
        return None
    a = body.get("a_terminations")
    b = body.get("b_terminations")
    if not a or not b:
        # Either side empty (or absent) means we cannot build
        # a valid cable; NetBox would refuse anyway.
        return (
            "cable body has no resolvable terminations on at "
            "least one side, skipping; the source row's "
            "interface endpoints did not import successfully"
        )
    return None


def upsert(
    http: NetboxHTTP,
    *,
    content_type: str,
    natural_key: NaturalKey,
    body: Mapping[str, Any],
    index: NKIndex,
    registry: NKRegistry,
    auditor: Any = None,
) -> UpsertResult:
    """Create-or-update one record on the destination.

    When `auditor` is supplied, every field rewritten by the
    write-side enum-dict coerce is recorded as a
    `BYPASS_COERCED` audit event so the operator can inspect
    which records the import-side coerce touched (BUG-01b).
    """

    endpoint = CONTENT_TYPE_ENDPOINTS.get(content_type)
    if endpoint is None:
        return UpsertResult(
            outcome=UpsertOutcome.FAILED,
            content_type=content_type,
            natural_key=natural_key,
            destination_id=None,
            message=f"no endpoint registered for {content_type}",
        )

    # precondition: skip rows whose body cannot
    # form a legal POST (e.g. a cable with no resolvable
    # endpoints on either side). NetBox would refuse with an
    # opaque `__all__` error; the SKIPPED outcome makes the
    # situation clean in the audit summary instead.
    skip_reason = _record_is_structurally_incomplete(content_type, body)
    if skip_reason is not None:
        return UpsertResult(
            outcome=UpsertOutcome.SKIPPED,
            content_type=content_type,
            natural_key=natural_key,
            destination_id=None,
            message=skip_reason,
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
        # also filter custom_fields keys against the
        # destination's known list so a look-ahead that fires
        # before the extras.customfield phase does not hit a
        # cascade of "Custom field X does not exist" rejections.
        coerced_body, coerced_fields = _coerce_body_for_write(
            body, drop_nones=True
        )
        post_body = _filter_custom_fields(coerced_body, http, content_type)
        if auditor is not None and coerced_fields:
            _record_bypass_coerced(
                auditor, content_type, natural_key, coerced_fields,
            )
        try:
            created = http.post(endpoint, post_body)
        except Exception as exc:  # noqa: BLE001 - surface anything to audit
            # some POST failures are not tool bugs,
            # they reflect destination policy (e.g. IPRange
            # overlap refused by ENFORCE_GLOBAL_UNIQUE). The
            # classifier returns an explanation string for
            # those cases and the outcome becomes SKIPPED so
            # the operator sees a clean count instead of a
            # failure that looks like nbsnap's bug.
            skip_reason = _classify_post_failure(content_type, str(exc))
            if skip_reason is not None:
                return UpsertResult(
                    outcome=UpsertOutcome.SKIPPED,
                    content_type=content_type,
                    natural_key=natural_key,
                    destination_id=None,
                    message=skip_reason,
                )
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
        coerced_diff, patch_coerced_fields = _coerce_body_for_write(diff)
        if auditor is not None and patch_coerced_fields:
            _record_bypass_coerced(
                auditor, content_type, natural_key, patch_coerced_fields,
            )
        http.patch(
            f"{endpoint}{existing_id}/",
            _filter_custom_fields(coerced_diff, http, content_type),
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


def _record_bypass_coerced(
    auditor: Any, content_type: str,
    natural_key: NaturalKey, fields: list[str],
) -> None:
    """Emit one BYPASS_COERCED audit event per coerced field.

    The auditor dedups on the standard quadruple so a record
    with five coerced fields produces five distinct events
    (different field names); a record processed twice
    (e.g. via the look-ahead path then the main phase) only
    counts each (record, field) once.
    """
    from nbsnap.import_.audit import DropCategory, DropEvent

    for field_name in fields:
        auditor.record(DropEvent(
            category=DropCategory.BYPASS_COERCED,
            child_content_type=content_type,
            child_nk=tuple(natural_key) if natural_key else (),
            field_name=field_name,
            target_content_type=content_type,
            target_nk=tuple(natural_key) if natural_key else (),
            message="snapshot value collapsed by import-side enum-dict coerce",
        ))


def _coerce_body_for_write(
    body: Mapping[str, Any], *, drop_nones: bool = False
) -> tuple[dict[str, Any], list[str]]:
    """Defensive write-side body coercion.

    Two transforms applied, both safe to run on a body that has
    already been resolved by `_resolve_body`:

    1. **Enum-dict collapse**, the canonical fix lives on the
       export side
       (`nbsnap.export.extractor._collapse_enum_dict`), but we
       coerce here so a legacy snapshot exported before that fix
       can still import via `--allow-enum-dict-bypass`. Fresh
       snapshots are flat already, this is a no-op for them.

    2. **None drop (opt-in via `drop_nones=True`)**,
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
    coerced_fields: list[str] = []
    for k, v in body.items():
        coerced = _collapse_enum_dict(v)
        if coerced is not v:
            # The enum-dict shape was actually collapsed.
            # Caller audits these as BYPASS_COERCED so the
            # operator sees per-field forensic detail.
            coerced_fields.append(k)
        if drop_nones and coerced is None:
            continue
        out[k] = coerced
    return out, coerced_fields
