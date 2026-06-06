"""Write-time body preparation chain.

The destination NetBox refuses certain body shapes that the
snapshot carries through unchanged from a GET-response form:

1. **Enum-dict shape.** GET returns
   `{"status": {"value": "active", "label": "Active"}}` for
   choice fields; POST/PATCH require the bare value
   `{"status": "active"}`.
2. **Explicit `null` on certain fields.** Some nullable FKs
   (`dcim.cable.profile`) are refused with HTTP 400
   `field may not be blank` when the body carries an
   explicit `null`. Dropping the key entirely tells NetBox
   to use the field's default.
3. **Unknown custom-field keys.** A look-ahead that fires
   before the customfield phase ran will carry CF keys whose
   definitions are not yet on the destination; the filter
   drops them. Once the customfield phase completes the
   filter sees the full registry and stops stripping.
4. **Planner-deferred FK fields.** The cycle-breaker pulls
   fields like `Device.primary_ip4` out of the POST body so
   the create lands cleanly; Phase-2 PATCHes them in once
   both endpoints exist.

`BodyPreparer` collects the four transforms behind one
entry point so each call site (POST in `upsert`, PATCH in
`upsert`, look-ahead in `lookahead.resolve_or_create`) shares
the same chain.

The chain runs in a fixed order because each step depends on
the output of the one before:

* enum-dict collapse -> the field is a scalar.
* None drop -> a scalar `None` is removed; CF filter never
  sees it.
* CF filter -> unknown keys removed before the deferred
  strip looks at the body, so a deferred field whose value
  is a CF cannot end up half-removed.
* Deferred strip -> the queue side-effect lands last so the
  CF filter cannot accidentally drop a deferred field's CF
  half-mapping.

REFACTOR-03a lands the class with steps 1 and 2 only. The
remaining steps stay in `upsert.py` / `driver.py` until
REFACTOR-03b moves them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nbsnap.snapshot import collapse_enum_dict


class BodyPreparer:
    """Run a sequence of write-time body coercions.

    Stateful so a single instance can be reused across many
    records of the same content type. Step 3 (CF filter) and
    step 4 (deferred strip) need additional inputs from the
    surrounding driver, so the constructor takes them as
    optional handles. None handles disable the corresponding
    step, keeping REFACTOR-03a a strict refactor of steps 1
    and 2.
    """

    def __init__(self, *, drop_nones: bool = False) -> None:
        self._drop_nones = drop_nones

    def prepare(
        self,
        content_type: str,
        body: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Run the chain. Returns `(prepared_body, coerced_fields)`.

        `coerced_fields` is the list of field names rewritten
        by the enum-dict collapse, used by callers for the
        BUG-01b audit emission.
        """

        out: dict[str, Any] = {}
        coerced_fields: list[str] = []
        for k, v in body.items():
            coerced = collapse_enum_dict(v)
            if coerced is not v:
                coerced_fields.append(k)
            if self._drop_nones and coerced is None:
                continue
            out[k] = coerced
        # content_type intentionally unused in the current
        # chain; REFACTOR-03b moves the CF filter here and
        # consumes it.
        _ = content_type
        return out, coerced_fields
