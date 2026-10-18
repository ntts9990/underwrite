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
    bind_deepeval_accounting,
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


def decode_validate_pack_bytes(raw: bytes, *, version: str | None = None) -> dict[str, object]:
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
    selected_schema = AUTO_PACK_SCHEMA if version is None else "deepeval_metric_bindings.v1"
    if type(document.get("schema")) is not str:
        raise DeepevalBindingError(
            "INVALID_INPUT", "DEEPEVAL_BINDING_SCHEMA_INVALID", "/bindings/schema"
        )
    if document["schema"] != selected_schema:
        raise DeepevalBindingError(
            "UNSUPPORTED_PROFILE", "DEEPEVAL_BINDING_PACK_SCOPE_UNSUPPORTED", "/bindings/schema"
        )
    _validate_document(
        document,
        selected_schema,
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


def _check_version(version: str | None) -> None:
    if version is not None and version != "4.2.8":
        raise DeepevalBindingError(
            "UNSUPPORTED_FORMAT", "DEEPEVAL_BINDING_VERSION_UNSUPPORTED", "/version"
        )


def _bind(
    accounting: dict[str, object], pack: dict[str, object], pack_raw_hash: str, version: str | None
) -> dict[str, object]:
    result = (
        bind_deepeval_accounting_auto(accounting, pack, pack_raw_hash=pack_raw_hash)
        if version is None
        else bind_deepeval_accounting(accounting, pack)
    )
    _validate_document(
        result,
        AUTO_RESULT_SCHEMA if version is None else "evidence_result.v1",
        "INTERNAL_ERROR",
        "DEEPEVAL_BINDING_RESULT_INVALID",
        "/result",
    )
    return result


def audit_deepeval_bound_bytes(
    source_raw: bytes, pack_raw: bytes, *, version: str | None = None
) -> dict[str, object]:
    """Analyze one complete captured pair without reopening either input."""
    _check_version(version)
    pack = decode_validate_pack_bytes(pack_raw, version=version)
    try:
        accounting = audit_deepeval_bytes(source_raw, version=version)
    except DeepevalFileError as exc:
        raise DeepevalBindingError(exc.code, exc.reason, exc.location) from None
    return _bind(accounting, pack, digest_bytes(pack_raw), version)


def audit_deepeval_bound_file(
    path: Path, bindings_path: Path, *, version: str | None = None
) -> dict[str, object]:
    """Validate the owner pack before opening the pinned TestRun file."""
    _check_version(version)
    pack_raw = read_pack_bytes(bindings_path)
    pack = decode_validate_pack_bytes(pack_raw, version=version)
    try:
        accounting = audit_deepeval_file(path, version=version)
    except DeepevalFileError as exc:
        raise DeepevalBindingError(exc.code, exc.reason, exc.location) from None
    return _bind(accounting, pack, digest_bytes(pack_raw), version)
