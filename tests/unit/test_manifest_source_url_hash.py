"""SEC-04a: ``Manifest.source_url_hash`` does not leak the literal URL.

Background
----------
Before SEC-04a the manifest persisted ``source_url`` as the literal
``http://...`` of the source NetBox. That contradicted the
"network-model-only, no install-local data" scope banner in
``CLAUDE.md`` and gave anyone with a leaked snapshot the source's
network coordinates. SEC-04a replaced the field with a short
sha256 fingerprint; this file pins the behaviour we depend on:

1. The same URL always produces the same hash (provenance).
2. Different URLs produce different hashes (no collision in normal
   operator inputs).
3. Neither ``repr()`` nor the JSON-serialisable representation of
   the manifest contains any ``http://`` or ``https://`` substring.

The last assertion is the security-critical one: any future field
that accidentally carries a URL would break the test.
"""

from __future__ import annotations

from dataclasses import asdict

from nbsnap.snapshot import (
    SOURCE_URL_HASH_LENGTH,
    Manifest,
    compute_source_url_hash,
)


def test_same_url_yields_same_hash() -> None:
    assert compute_source_url_hash("https://netbox.example/") == (
        compute_source_url_hash("https://netbox.example/")
    )


def test_different_urls_yield_different_hashes() -> None:
    a = compute_source_url_hash("https://netbox.example/")
    b = compute_source_url_hash("https://other.example/")
    assert a != b


def test_hash_length_is_locked() -> None:
    h = compute_source_url_hash("https://x/")
    assert len(h) == SOURCE_URL_HASH_LENGTH
    # All hex, no other characters slipped in.
    assert all(c in "0123456789abcdef" for c in h)


def test_repr_does_not_contain_the_literal_url() -> None:
    """A dev-time ``repr(manifest)`` must not surface ``http://...``.

    Any new field that accidentally carried a URL would break this.
    """

    manifest = Manifest(
        source_url_hash=compute_source_url_hash("https://netbox.example/"),
        netbox_version="4.6.2",
    )
    representation = repr(manifest)
    assert "http://" not in representation
    assert "https://" not in representation


def test_to_dict_does_not_contain_the_literal_url() -> None:
    """The JSON-serialisable shape must not contain ``http(s)://`` either.

    The dataclass ``asdict`` is what :meth:`Manifest.write` ultimately
    serialises, so this is the on-disk shape too.
    """

    manifest = Manifest(
        source_url_hash=compute_source_url_hash("https://netbox.example/"),
        netbox_version="4.6.2",
    )
    data = asdict(manifest)
    flat = repr(data)
    assert "http://" not in flat
    assert "https://" not in flat
