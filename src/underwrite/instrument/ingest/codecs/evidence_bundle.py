"""Read native evidence bundles while preserving unverified source claims."""

from collections.abc import Mapping
from types import MappingProxyType

from underwrite.instrument.ingest.codecs._shape import Shape
from underwrite.instrument.ingest.project import Projection
from underwrite.instrument.ingest.transport import Codec, UnsupportedFormat


class EvidenceBundlePayloadError(ValueError):
    """A native evidence bundle lacks its minimum consumed structure."""


_SHAPE = Shape(EvidenceBundlePayloadError, "EVIDENCE_BUNDLE")


def read_evidence_bundle_v1(value: object) -> Projection:
    """Project declared evidence without verifying its readings or references."""
    payload = _SHAPE.as_object(value, "bundle")
    if payload.get("schema_version") != "underwrite.evidence-bundle.v1":
        raise UnsupportedFormat("UNSUPPORTED_FORMAT")
    manifest = _SHAPE.as_object(payload.get("manifest"), "manifest")
    _SHAPE.identifier(manifest, "run_id")
    for key in ("sources", "readings", "availability"):
        for item in _SHAPE.as_array(payload.get(key), key):
            _SHAPE.as_object(item, key)
    return Projection("eval_run", payload)


CODECS: Mapping[tuple[str, str], Codec] = MappingProxyType(
    {("underwrite.evidence-bundle", "v1"): read_evidence_bundle_v1}
)
