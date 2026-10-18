"""Read one native DeepEval TestRun for selected, minimized trace indexing."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.evidence.deepeval_accounting import DeepevalAccountingError
from underwrite.instrument.evidence.deepeval_trace import (
    SCHEMA,
    SOURCE_FORMAT,
    SOURCE_VERSION,
    index_deepeval_trace,
)
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    instrument_diagnostic,
)
from underwrite.instrument.ingest.deepeval_accounting import DeepevalFileError, read_deepeval_file
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import DecodeError, decode_json


def _version(version: str) -> None:
    if version != SOURCE_VERSION:
        raise DeepevalFileError(
            "UNSUPPORTED_FORMAT", "DEEPEVAL_TRACE_VERSION_UNSUPPORTED", "/version"
        )


def _validator() -> Draft202012Validator:
    try:
        return packaged_validator(SCHEMA)
    except SchemaConfigurationError:
        raise DeepevalFileError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT") from None


def _index(raw: bytes, validator: Draft202012Validator) -> dict[str, object]:
    if len(raw) > DEFAULT_MAX_BYTES:
        raise DeepevalFileError("INPUT_LIMIT_EXCEEDED", "BYTE_LIMIT_EXCEEDED", "/file")
    try:
        payload = decode_json(raw, DEFAULT_MAX_DEPTH)
    except DecodeError as exc:
        diagnostic = instrument_diagnostic(exc, "ingest")
        raise DeepevalFileError(
            cast(str, diagnostic["code"]),
            cast(str, diagnostic["reason"]),
            "/file",
        ) from None
    try:
        counted = index_deepeval_trace(payload)
    except DeepevalAccountingError as exc:
        raise DeepevalFileError(exc.code, exc.reason, exc.location) from None
    report = {
        "schema": SCHEMA,
        "source": {
            "format": SOURCE_FORMAT,
            "format_version": SOURCE_VERSION,
            "claims_verified": False,
        },
        "raw_hash": digest_bytes(raw),
        **counted,
    }
    try:
        validator.validate(report)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError:
        raise DeepevalFileError("INTERNAL_ERROR", "DEEPEVAL_TRACE_RESULT_INVALID") from None
    except Exception:
        raise DeepevalFileError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT") from None
    return report


def audit_deepeval_trace_bytes(raw: bytes, *, version: str | None = None) -> dict[str, object]:
    """Analyze supplied original bytes without materializing a canonical source copy."""
    if version is None:
        from underwrite.instrument.ingest.deepeval_compat import audit_deepeval_compatible_bytes

        return audit_deepeval_compatible_bytes(raw, view="trace-index")
    _version(version)
    return _index(raw, _validator())


def audit_deepeval_trace_file(path: Path, *, version: str | None = None) -> dict[str, object]:
    """Validate selected contract, then capture the regular source file once."""
    if version is None:
        from underwrite.instrument.ingest.deepeval_compat import audit_deepeval_compatible_file

        return audit_deepeval_compatible_file(path, view="trace-index")
    _version(version)
    validator = _validator()
    return _index(read_deepeval_file(path), validator)
