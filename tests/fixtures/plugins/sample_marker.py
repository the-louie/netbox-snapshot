"""Tiny fixture plugin used by ARCH-04d integration test.

Registers a unique NKSpec for ``test_marker.canary`` so the
integration test can assert the plugin actually reached the
driver's NK registry without needing a real NetBox stack.
"""

from nbsnap.natkey.model import NKField, NKSpec, Strategy
from nbsnap.plugins.api import Registrar


class _MarkerPlugin:
    name = "marker"
    version = "0.0.1"

    def register(self, registrar: Registrar) -> None:
        registrar.add_nkspec(
            NKSpec(
                content_type="test_marker.canary",
                strategy=Strategy.SLUG,
                fields=(NKField("slug"),),
            )
        )


plugin = _MarkerPlugin()
