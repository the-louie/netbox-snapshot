"""Terminal progress UI helpers (FEAT-29).

A tiny, dependency-free progress reporter that draws a single
status line when stdout is a TTY and goes silent when it is not.
The goal is "you can see something is happening" rather than a
fancy bar; a real tqdm dependency would only bloat the install.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager


@contextmanager
def report(label: str, total: int):  # type: ignore[no-untyped-def]
    """Context manager yielding a `tick` function.

    Inside the block, call `tick(count)` whenever progress
    advances. We refresh at most once every ~50ms to avoid
    flooding the terminal in tight loops.
    """

    state = {"last": 0.0, "shown": 0}
    is_tty = sys.stderr.isatty()

    def tick(count: int) -> None:
        if not is_tty:
            return
        # `time.monotonic()` is cheap enough that polling it on
        # every call does not hurt the inner loop.
        import time

        now = time.monotonic()
        if now - state["last"] < 0.05 and count != total:
            return
        state["last"] = now
        state["shown"] = count
        bar = _bar(count, total)
        sys.stderr.write(f"\r{label}: {count}/{total} {bar}")
        sys.stderr.flush()

    try:
        yield tick
    finally:
        if is_tty:
            sys.stderr.write("\n")
            sys.stderr.flush()


def _bar(count: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return ""
    filled = int(width * count / total)
    return "[" + "#" * filled + "-" * (width - filled) + "]"
