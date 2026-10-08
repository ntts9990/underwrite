"""The native codec registry returns its own mapping and exact codec owner."""

from underwrite.instrument.ingest import formats
from underwrite.instrument.ingest.codecs import evidence_bundle


def test_registry_preserves_owner_and_returns_owned_mapping() -> None:
    actual = formats.native_codecs()
    assert actual == evidence_bundle.CODECS
    assert set(actual) == {("underwrite.evidence-bundle", "v1")}
    actual.clear()
    assert formats.native_codecs() == evidence_bundle.CODECS
