"""Upsert by NK (FEAT-21a/b).

The upsert routine:

1. Looks up the record by NK on the destination's index.
2. If absent, POSTs the record and inserts the new id into the
   index.
3. If present, compares each field for equality. When the
   destination already matches the snapshot body, the call is a
   noop. Otherwise issue a PATCH with only the differing fields
   so the destination's audit log shows the minimal diff.
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
        post_body = _coerce_body_for_write(body, drop_nones=True)
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
        http.patch(f"{endpoint}{existing_id}/", _coerce_body_for_write(diff))
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
