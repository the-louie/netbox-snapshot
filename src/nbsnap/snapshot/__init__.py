"""The on-disk snapshot contract (ARCH-01).

This package owns the **data contract** between :mod:`nbsnap.export`
and :mod:`nbsnap.import_`. Before ARCH-01 the manifest dataclass,
the content-type to filename mapping, and the enum-dict coercion all
lived under :mod:`nbsnap.export` and :mod:`nbsnap.import_` imported
from there, which made the two peers asymmetrically coupled.

ARCH-01's plan is staged across seven sub-tickets:

* ARCH-01a (this scaffold) creates the empty package and the matching
  test directory so subsequent sub-tickets have a destination to move
  into.
* ARCH-01b moves :class:`Manifest` and the manifest filename constant
  into :mod:`nbsnap.snapshot.manifest`.
* ARCH-01c moves ``CONTENT_TYPE_FILES`` and ``relative_path`` into
  :mod:`nbsnap.snapshot.layout`.
* ARCH-01d promotes the previously private ``_collapse_enum_dict``
  helper to :mod:`nbsnap.snapshot.coerce.collapse_enum_dict`.
* ARCH-01e/f migrate the consumers and remove the temporary
  back-compat re-exports.
* ARCH-01g locks the layering invariant with an ``ast`` regression
  test.

While the package is still empty (sub-ticket ARCH-01a state) the
``__all__`` is the empty list. Later sub-tickets append to it as they
move contracts in. Keeping the list explicit makes it obvious from a
``dir(nbsnap.snapshot)`` what the package promises and what is still
private.
"""

from __future__ import annotations

from nbsnap.snapshot.coerce import ENUM_DICT_KEYS, collapse_enum_dict
from nbsnap.snapshot.layout import CONTENT_TYPE_FILES, relative_path
from nbsnap.snapshot.manifest import (
    MANIFEST_FILENAME,
    SOURCE_URL_HASH_LENGTH,
    Manifest,
    compute_source_url_hash,
)

__all__: list[str] = [
    "CONTENT_TYPE_FILES",
    "ENUM_DICT_KEYS",
    "MANIFEST_FILENAME",
    "Manifest",
    "SOURCE_URL_HASH_LENGTH",
    "collapse_enum_dict",
    "compute_source_url_hash",
    "relative_path",
]
