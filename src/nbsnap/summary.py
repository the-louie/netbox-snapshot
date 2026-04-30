"""End-of-run summary table (FEAT-30).

Renders an aligned ASCII table on stderr listing per-content-type
counts and outcomes. The format is intentionally compact so a
typical run fits on one screen.
"""

from __future__ import annotations

from collections.abc import Mapping
from io import StringIO


def render_summary(
    counts: Mapping[str, int], outcomes: Mapping[str, Mapping[str, int]] | None = None
) -> str:
    """Return an ASCII table summarising the run."""

    out = StringIO()
    out.write("content type            count   ")
    if outcomes:
        out.write("created  updated  noop  failed\n")
    else:
        out.write("\n")
    out.write("-" * 60 + "\n")
    for ct in sorted(counts.keys()):
        line = f"{ct:<22}  {counts[ct]:>5}"
        if outcomes is not None:
            o = outcomes.get(ct, {})
            line += (
                f"   {o.get('created', 0):>5}  "
                f"{o.get('updated', 0):>5}  "
                f"{o.get('noop', 0):>4}  "
                f"{o.get('failed', 0):>5}"
            )
        out.write(line + "\n")
    return out.getvalue()
