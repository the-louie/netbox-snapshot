"""Structured logging setup (FEAT-28a) and event helpers (FEAT-28b).

We surface log lines in two formats:

* **Human**: short single-line records with the level and module
  for terminal-friendly reading.
* **JSON**: one JSON object per line, suitable for ingest into
  the operator's log aggregator.

The formatter to use is picked at CLI start time per the
`--log-format` flag added in `cli.py` (defaults to `human`).

`emit_write_event` is a thin helper that records each upsert
result as a JSON object alongside the normal log stream so an
operator can grep "what changed" without re-reading the audit
file.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """Render every log record as a one-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        event = getattr(record, "event", None)
        if isinstance(event, dict):
            payload["event"] = event
        return json.dumps(payload, sort_keys=True, default=str)


def configure(level: int = logging.INFO, fmt: str = "human") -> None:
    """Reconfigure the root logger.

    Call this once at CLI start. Idempotent: clearing existing
    handlers means a second call replaces the format without
    duplicating output.
    """

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(stream=sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(levelname)s %(name)s: %(message)s",
            )
        )
    root.addHandler(handler)


def emit_write_event(
    logger: logging.Logger,
    *,
    content_type: str,
    natural_key: Any,
    outcome: str,
    message: str = "",
) -> None:
    """Log a per-write event with structured fields the JSON formatter sees."""

    logger.info(
        "%s %s -> %s %s",
        content_type,
        natural_key,
        outcome,
        message,
        extra={
            "event": {
                "kind": "write",
                "content_type": content_type,
                "natural_key": natural_key,
                "outcome": outcome,
                "message": message,
            }
        },
    )
