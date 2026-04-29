"""TEST-01b, content-type drift assertion (informational, per Q14)."""

from __future__ import annotations

import logging

import pytest

from nbsnap.http.client import NetboxHTTP
from nbsnap.schema.content_types import ContentTypeCache

from .conftest import DEST_TOKEN, DEST_URL, SOURCE_TOKEN, SOURCE_URL

logger = logging.getLogger(__name__)


@pytest.mark.usefixtures("require_stack")
def test_content_type_ids_can_diverge_between_stacks(caplog) -> None:
    """Informational pass: log whether ids match or diverge.

    Q14 burndown decision: this is informational, not gating. The
    later round-trip tests exercise the ContentType translation
    code regardless of whether ids happen to match here.
    """

    source = ContentTypeCache.fetch(NetboxHTTP(SOURCE_URL, SOURCE_TOKEN, verify_tls=False))
    dest = ContentTypeCache.fetch(NetboxHTTP(DEST_URL, DEST_TOKEN, verify_tls=False))

    with caplog.at_level(logging.INFO):
        try:
            src_device_id = source.id_for("dcim", "device")
            dst_device_id = dest.id_for("dcim", "device")
        except KeyError as exc:
            pytest.skip(f"dcim.device missing from one stack: {exc!s}")

        if src_device_id != dst_device_id:
            logger.info(
                "content-type ids diverge between stacks (source=%d, dest=%d), "
                "translation path is exercised",
                src_device_id,
                dst_device_id,
            )
        else:
            logger.info(
                "content-type ids match between stacks (id=%d), "
                "translation path runs transparently",
                src_device_id,
            )
