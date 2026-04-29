"""Per-endpoint extractor (FEAT-11a/b/c/d).

The extractor is the workhorse of the export engine. Given an
endpoint, an OpenAPI schema, a natural-key registry, and a
parent-lookup map, it walks every record and emits a sequence of
snapshot rows (one dict per record).

Pipeline per record:

1. **Allowlist filter** (FEAT-11b). Drop fields that are not in
   the `write_allowlist` for the content type so the snapshot
   does not carry read-only data the importer will ignore.
2. **FK rewrite** (FEAT-11c via fk_rewrite.py). Replace every
   FK id with the natural-key tuple from the registry.
3. **Install-local classifier** (FEAT-11d via install_local.py).
   Drop records flagged as install-local and write a Flag entry
   to the flag log.

The extractor does not write to disk. The writer (FEAT-14a) takes
the yielded dicts and lays them out as JSONL.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from nbsnap.export.fk_rewrite import (
    ParentLookup,
    rewrite_m2m,
    rewrite_polymorphic,
    rewrite_simple_fk,
)
from nbsnap.export.install_local import Flag, is_install_local
from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.resolver import resolve
from nbsnap.schema.openapi import OpenAPI


@dataclass(frozen=True)
class ExtractedRow:
    """One record after the extractor's three-step transform."""

    content_type: str
    natural_key: tuple[Any, ...]
    body: dict[str, Any]


def extract(
    content_type: str,
    records: Iterator[Mapping[str, Any]],
    *,
    openapi: OpenAPI,
    registry: NKRegistry,
    parent_lookup: ParentLookup,
    source_url: str,
) -> Iterator[tuple[ExtractedRow | None, Flag | None]]:
    """Yield `(extracted_row | None, flag | None)` per source record.

    `extracted_row` is `None` when the record is install-local;
    `flag` is the audit entry in that case. Both being non-None at
    once would be a bug, the caller asserts at most one is set.
    """

    allowlist = openapi.write_allowlist(content_type)

    for record in records:
        flag = is_install_local(content_type, record, source_url)
        if flag is not None:
            yield None, flag
            continue

        try:
            nk = resolve(registry, content_type, record, parent_lookup=parent_lookup)
        except (KeyError, ValueError):
            # Cannot compute NK; skip the record and surface a flag.
            yield (
                None,
                Flag(
                    content_type=content_type,
                    natural_key=(),
                    field="natural_key",
                    reason="missing or empty NK field",
                ),
            )
            continue

        body = _apply_allowlist(record, allowlist)
        body = _rewrite_fks(content_type, body, openapi, registry, parent_lookup)
        yield ExtractedRow(content_type=content_type, natural_key=nk, body=body), None


def _apply_allowlist(
    record: Mapping[str, Any], allowlist: frozenset[str]
) -> dict[str, Any]:
    """Keep only the fields the destination will accept."""
    return {k: v for k, v in record.items() if k in allowlist}


def _rewrite_fks(
    content_type: str,
    body: dict[str, Any],
    openapi: OpenAPI,
    registry: NKRegistry,
    parent_lookup: ParentLookup,
) -> dict[str, Any]:
    """Walk the record, rewrite every FK in place."""
    rewritten: dict[str, Any] = {}
    for field_name, value in body.items():
        spec = openapi.field_spec(content_type, field_name)
        if spec.fk_target is None:
            rewritten[field_name] = value
            continue
        if spec.is_m2m:
            rewritten[field_name] = rewrite_m2m(value, spec.fk_target, registry, parent_lookup)
            continue
        # Polymorphic generic FKs surface as a `{object_type, object_id}`
        # dict in NetBox; we treat them via the polymorphic rewriter.
        if isinstance(value, Mapping) and "object_type" in value:
            rewritten[field_name] = rewrite_polymorphic(value, registry, parent_lookup)
            continue
        rewritten[field_name] = rewrite_simple_fk(
            value, spec.fk_target, registry, parent_lookup
        )
    return rewritten
