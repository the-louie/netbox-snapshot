"""Categorised drop / defer audit for the import resolver (FEAT-36e).

Before this module landed, every FK that the resolver dropped
went to the same warning log line. The operator could see
"something was dropped" but had to dig through the file paths
and code to figure out whether it was a deliberate scope
choice or a real data issue.

Three categories carry meaningfully different operator weight:

* `OUT_OF_SCOPE`, the target content type is not in the
  snapshot at all (e.g. `dcim.region`, `ipam.vrf`). The
  `CLAUDE.md` "network model only" banner excludes these by
  design. Operator does not need to act.
* `MISSING_FROM_SOURCE`, the target IS in scope, but the NK
  the source references does not exist anywhere. Either the
  source has a stale reference, or export missed something.
  Worth investigating.
* `DEFERRED_TO_PHASE2`, the resolver hit a cycle and pushed
  the FK onto the Phase-2 queue. Not an error, just observable
  evidence of the cycle-breaking path doing its job.

Audit output goes to two destinations: a stderr summary table
at the end of the run and an `audit.jsonl` file the operator
can grep, parse, or archive.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DropCategory(Enum):
    """Why an FK reference did not make it to the destination."""

    OUT_OF_SCOPE = "out_of_scope"
    MISSING_FROM_SOURCE = "missing_from_source"
    DEFERRED_TO_PHASE2 = "deferred_to_phase2"
    # An earlier look-ahead create attempt for the referenced
    # record itself returned FAILED. The destination POST was
    # refused by NetBox (e.g. validation error), not by an
    # nbsnap bug, so the reference is not "missing from
    # source", it is "we could not place it". This category
    # exists because mis-bucketing as MISSING_FROM_SOURCE
    # inflates the operator's data-quality concern when the
    # real issue is destination policy or a tool gap.
    UPSERT_FAILED = "upsert_failed"
    # Variant of UPSERT_FAILED for HTTP 5xx (transient) refusals.
    # The destination's POST failed for an environment reason
    # (database under load, restart in progress), not a data
    # problem. Operators usually rerun the import; nbsnap does
    # not cache 5xx failures so a rerun will retry the parent
    # before dropping. See FEAT-45b.
    UPSERT_FAILED_TRANSIENT = "upsert_failed_transient"
    # A field was rewritten by the import-side `_collapse_enum_dict`
    # coerce because the snapshot carries the legacy
    # `{value, label}` shape. The record imported successfully,
    # but the snapshot itself is stale and a re-export would
    # avoid the coerce. The audit log carries one event per
    # coerced field so an operator can verify which records
    # the bypass touched (BUG-01b).
    BYPASS_COERCED = "bypass_coerced"


@dataclass
class DropEvent:
    """One categorised FK drop, eventually written to audit.jsonl."""

    category: DropCategory
    child_content_type: str
    child_nk: tuple[Any, ...]
    field_name: str
    target_content_type: str
    target_nk: tuple[Any, ...]
    message: str = ""

    def to_json(self) -> dict[str, Any]:
        """Render as a JSON-serialisable dict.

        Tuples become lists because JSON has no tuple type. The
        `Auditor.write_jsonl` writer feeds this directly to
        `json.dumps`.
        """

        return {
            "category": self.category.value,
            "child": {
                "content_type": self.child_content_type,
                "nk": list(self.child_nk),
            },
            "field": self.field_name,
            "target": {
                "content_type": self.target_content_type,
                "nk": list(self.target_nk),
            },
            "message": self.message,
        }


@dataclass
class Auditor:
    """Accumulates drop events and renders the operator summary.

    Dedup is category-aware (see `BUG-02`):

    * For most categories the key is the
      `(child_ct, field, target_ct, target_nk)` quadruple, so
      a run that touches a thousand Devices each missing the
      same Region collapses to one audit entry.
    * For `DEFERRED_TO_PHASE2` the key is the
      `(child_ct, child_nk, field)` triple, mirroring the
      `_strip_deferred_fields_and_queue` work-queue key so the
      audit count and the Phase-2 patched count compare cleanly.
    """

    events: list[DropEvent] = field(default_factory=list)
    _seen: set[tuple[Any, ...]] = field(default_factory=set)

    def record(self, event: DropEvent) -> None:
        """Record one event with category-aware deduplication.

        For most categories the dedup key is the quadruple
        `(child_ct, field, target_ct, target_nk)`, so 1,000
        Devices each missing the same Region collapse to one
        audit entry.

        For `DEFERRED_TO_PHASE2` the key drops `target_ct` and
        `target_nk` and adds `child_nk` instead, giving
        `(child_ct, child_nk, field)`. This matches the
        deduplication that `_strip_deferred_fields_and_queue`
        applies to the Phase-2 work queue, so the audit count
        and the Phase-2 patched count compare cleanly.

        The first occurrence wins; later occurrences are
        silently dropped. We log at INFO level so the operator
        can crank verbosity up if they want every duplicate to
        surface.
        """

        if event.category is DropCategory.DEFERRED_TO_PHASE2:
            key: tuple[Any, ...] = (
                "deferred",
                event.child_content_type,
                event.child_nk,
                event.field_name,
            )
        else:
            key = (
                "default",
                event.child_content_type,
                event.field_name,
                event.target_content_type,
                event.target_nk,
            )
        if key in self._seen:
            return
        self._seen.add(key)
        self.events.append(event)
        logger.info(
            "[%s] %s.%s -> %s NK=%r",
            event.category.value,
            event.child_content_type,
            event.field_name,
            event.target_content_type,
            event.target_nk,
        )

    def write_jsonl(self, path: Path) -> None:
        """Persist the full event list to disk, one JSON object per line.

        An empty event list still produces an empty file so the
        operator's downstream tooling does not have to special-
        case "no audit log exists".
        """

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            for event in self.events:
                fp.write(json.dumps(event.to_json(), sort_keys=True) + "\n")

    def render_summary(self, *, limit: int = 10) -> str:
        """Build the stderr-friendly summary block.

        Format: leading "audit:" header, per-category counts, then
        a "top offending (content_type, field)" list. The list
        shows every distinct site when the total is `<= limit`, and
        is capped with an `... and N more (see audit log)` trailer
        when above.

        `limit` defaults to 10 so the terminal output stays
        digestible. The CLI's `--audit-summary-limit` flag overrides
        this for operators digging into a drop-heavy import.
        """

        if not self.events:
            return "  audit: no FK drops or defers.\n"

        by_category: Counter[str] = Counter(e.category.value for e in self.events)
        by_pair: defaultdict[tuple[str, str], int] = defaultdict(int)
        for ev in self.events:
            by_pair[(ev.child_content_type, ev.field_name)] += 1

        out = ["  audit:"]
        for cat in DropCategory:
            count = by_category.get(cat.value, 0)
            out.append(f"    {cat.value}: {count}")
        if limit > 0:
            ranked = sorted(by_pair.items(), key=lambda kv: -kv[1])
            top = ranked[:limit]
            out.append("    top offending (content_type, field):")
            for (ct, fld), n in top:
                out.append(f"      {ct}.{fld}: {n}")
            remainder = len(ranked) - len(top)
            if remainder > 0:
                out.append(f"      ... and {remainder} more (see audit log)")
        return "\n".join(out) + "\n"
