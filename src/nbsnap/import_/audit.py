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

    Records dedupe on the `(child_ct, field, target_ct,
    target_nk)` quadruple so a run that touches a thousand
    Devices each missing the same Region does not flood the log
    with a thousand identical lines, only one entry per missing
    target survives.
    """

    events: list[DropEvent] = field(default_factory=list)
    _seen: set[tuple[Any, ...]] = field(default_factory=set)

    def record(self, event: DropEvent) -> None:
        """Record one event; de-dupes on the quadruple key.

        The first occurrence wins; later occurrences are
        silently dropped. We log at INFO level so the operator
        can crank verbosity up if they want every duplicate to
        surface.
        """

        key = (
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

    def render_summary(self) -> str:
        """Build the stderr-friendly summary block.

        Format: leading "audit:" header, per-category counts, then
        a "top offending (content_type, field)" list capped at five
        entries to keep the terminal output digestible.
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
        top = sorted(by_pair.items(), key=lambda kv: -kv[1])[:5]
        out.append("    top offending (content_type, field):")
        for (ct, fld), n in top:
            out.append(f"      {ct}.{fld}: {n}")
        return "\n".join(out) + "\n"
