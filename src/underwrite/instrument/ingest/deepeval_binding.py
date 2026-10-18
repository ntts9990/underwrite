"""Bounded local declaration loading and DeepEval common-result composition."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.evidence.deepeval_binding import (
    AUTO_PACK_SCHEMA,
    AUTO_RESULT_SCHEMA,
    DeepevalBindingError,
    bind_deepeval_accounting_auto,
)
from underwrite.instrument.ingest.deepeval_accounting import (
    DeepevalFileError,
    audit_deepeval_bytes,
    audit_deepeval_file,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import (
    DecodeError,
    TransportError,
    decode_json,
    read_local_file,
)

PACK_MAX_BYTES = 128 * 1024
PACK_MAX_DEPTH = 32


def read_pack_bytes(bindings_path: Path) -> bytes:
    """Capture one complete bounded owner pack without parsing or reopening it."""
    try:
        return read_local_file(bindings_path, PACK_MAX_BYTES)
    except TransportError as exc:
        reason = str(exc)
        code = (
            "INPUT_LIMIT_EXCEEDED"
            if reason == "BYTE_LIMIT_EXCEEDED"
            else "UNSUPPORTED_PLATFORM"
            if reason == "UNSUPPORTED_PLATFORM"
            else "SOURCE_READ_FAILED"
        )
        raise DeepevalBindingError(code, "DEEPEVAL_BINDING_READ_FAILED", "/bindings") from None


def decode_validate_pack_bytes(raw: bytes) -> dict[str, object]:
    """Apply the same byte, depth, and schema admission to captured pack bytes."""
    if len(raw) > PACK_MAX_BYTES:
        raise DeepevalBindingError(
            "INPUT_LIMIT_EXCEEDED", "DEEPEVAL_BINDING_READ_FAILED", "/bindings"
        )
    try:
        pack = decode_json(raw, PACK_MAX_DEPTH)
    except DecodeError as exc:
        code = "INPUT_LIMIT_EXCEEDED" if str(exc) == "DEPTH_LIMIT_EXCEEDED" else "INVALID_INPUT"
        raise DeepevalBindingError(code, "DEEPEVAL_BINDING_JSON_INVALID", "/bindings") from None
    if type(pack) is not dict:
        raise DeepevalBindingError("INVALID_INPUT", "DEEPEVAL_BINDING_OBJECT_REQUIRED", "/bindings")
    document = cast(dict[str, object], pack)
    if type(document.get("schema")) is not str:
        raise DeepevalBindingError(
            "INVALID_INPUT", "DEEPEVAL_BINDING_SCHEMA_INVALID", "/bindings/schema"
        )
    if document["schema"] != AUTO_PACK_SCHEMA:
        raise DeepevalBindingError(
            "UNSUPPORTED_PROFILE", "DEEPEVAL_BINDING_PACK_SCOPE_UNSUPPORTED", "/bindings/schema"
        )
    _validate_document(
        document,
        AUTO_PACK_SCHEMA,
        "INVALID_INPUT",
        "DEEPEVAL_BINDING_SCHEMA_INVALID",
        "/bindings",
    )
    return document


def _validate_document(
    document: dict[str, object],
    schema: str,
    invalid_code: str,
    invalid_reason: str,
    location: str,
) -> None:
    """Validate at the ingest boundary with finite caller-specific refusals."""
    try:
        packaged_validator(schema).validate(document)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError:
        raise DeepevalBindingError(invalid_code, invalid_reason, location) from None
    except SchemaConfigurationError:
        raise DeepevalBindingError(
            "SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT", location
        ) from None
    except Exception:
        raise DeepevalBindingError(
            "SCHEMA_CONFIGURATION_ERROR", "SCHEMA_EVALUATION_FAILED", location
        ) from None


def _bind(
    accounting: dict[str, object], pack: dict[str, object], pack_raw_hash: str
) -> dict[str, object]:
    result = bind_deepeval_accounting_auto(accounting, pack, pack_raw_hash=pack_raw_hash)
    _validate_document(
        result,
        AUTO_RESULT_SCHEMA,
        "INTERNAL_ERROR",
        "DEEPEVAL_BINDING_RESULT_INVALID",
        "/result",
    )
    return result


def audit_deepeval_bound_bytes(source_raw: bytes, pack_raw: bytes) -> dict[str, object]:
    """Analyze one complete captured pair without reopening either input."""
    pack = decode_validate_pack_bytes(pack_raw)
    try:
        accounting = audit_deepeval_bytes(source_raw)
    except DeepevalFileError as exc:
        raise DeepevalBindingError(exc.code, exc.reason, exc.location) from None
    return _bind(accounting, pack, digest_bytes(pack_raw))


def audit_deepeval_bound_file(path: Path, bindings_path: Path) -> dict[str, object]:
    """Validate the owner pack before opening the TestRun file."""
    pack_raw = read_pack_bytes(bindings_path)
    pack = decode_validate_pack_bytes(pack_raw)
    try:
        accounting = audit_deepeval_file(path)
    except DeepevalFileError as exc:
        raise DeepevalBindingError(exc.code, exc.reason, exc.location) from None
    return _bind(accounting, pack, digest_bytes(pack_raw))
