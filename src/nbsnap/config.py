"""`.env` auto-loader, mirrors the convention used in `__reference/nb2kea/`.

Operators routinely stash NetBox tokens in a `/workspace/.env` file
so they do not have to keep exporting variables. This module looks
for that file when `nbsnap` starts and copies any `KEY=VALUE` pairs
into `os.environ`, without overriding values the shell already set.

The parser is deliberately tiny on purpose:

* No quoting (the nb2kea `.env` files do not use quotes).
* No variable substitution (`$FOO` stays literal).
* No multi-line values.

If we ever need a richer dialect we can pull in `python-dotenv`,
for now the small implementation here keeps the dependency surface
to zero and matches the format the operator already uses.
"""

from __future__ import annotations

import os
from pathlib import Path

# Public name for the file we look for. Keeping it as a module
# constant makes the search behaviour easy to monkey-patch in
# tests, and easy to swap if a future ticket wants `.env.local`
# precedence rules.
ENV_FILENAME = ".env"


def _parse_line(raw: str) -> tuple[str, str] | None:
    """Parse a single `.env` line into a `(key, value)` tuple.

    Returns `None` for blanks and comments so the caller can simply
    skip them. Lines without an `=` are treated as malformed and
    ignored, matching the nb2kea behaviour (defensive, never raise
    on a hand-edited file).
    """
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    key = key.strip()
    if not key:
        # `=value` lines are malformed but should not crash the loader.
        return None
    return key, value.strip()


def _find_env_file(start: Path) -> Path | None:
    """Walk upwards from `start`, return the first `.env` found.

    The walk stops at the filesystem root. We do not climb past the
    user's home directory by policy, an `.env` in `/home/<user>/.env`
    is still discovered if you launch `nbsnap` directly from a
    subdirectory, which matches nb2kea behaviour.
    """
    current = start.resolve()
    # Walk current then every parent. Using a for-loop with the
    # parents iterable makes the upward traversal explicit and easy
    # to read.
    for candidate in (current, *current.parents):
        env_path = candidate / ENV_FILENAME
        if env_path.is_file():
            return env_path
    return None


def load_dotenv(start: Path | None = None) -> Path | None:
    """Load `.env` into `os.environ`, return the path or `None`.

    Args:
        start: The directory to begin the upward search from.
            Defaults to `Path.cwd()`. Passing an explicit path is
            mostly useful in tests so the loader does not depend on
            the test runner's working directory.

    Behaviour notes:
        * Variables already present in `os.environ` are **not**
          overwritten. An explicit shell `export` always wins over
          the `.env` value. This is the nb2kea contract and operators
          rely on it for one-off overrides.
        * Malformed lines (no `=`) are silently skipped. We surface
          a clean error only when a downstream consumer demands a
          missing variable, not when a stale comment slips into the
          file.
    """
    base = Path.cwd() if start is None else Path(start)
    env_path = _find_env_file(base)
    if env_path is None:
        return None

    # `errors="replace"` keeps the loader from blowing up on a
    # single non-utf8 byte in a hand-edited file. The downstream
    # consumer will fail loudly if a value it actually needs is
    # corrupted.
    text = env_path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        parsed = _parse_line(raw)
        if parsed is None:
            continue
        key, value = parsed
        # The "shell wins" rule, implemented as an `if not in`. Doing
        # the check with `setdefault` would be slightly shorter but
        # `setdefault` returns the existing value, which we would
        # then have to discard. The explicit guard reads cleaner.
        if key not in os.environ:
            os.environ[key] = value

    return env_path
