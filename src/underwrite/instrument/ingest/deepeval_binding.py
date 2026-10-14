"""Bounded local declaration loading and DeepEval common-result composition."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from jsonschema.exceptions import ValidationError

from underwrite.instrument.evidence.deepeval_binding import (
    DeepevalBindingError,
    bind_deepeval_accounting,
)
from underwrite.instrument.ingest.deepeval_accounting import (
    DeepevalFileError,
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


def _read_pack(bindings_path: Path) -> dict[str, object]:
    try:
        raw = read_local_file(bindings_path, PACK_MAX_BYTES)
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
    try:
        pack = decode_json(raw, PACK_MAX_DEPTH)
    except DecodeError as exc:
        code = "INPUT_LIMIT_EXCEEDED" if str(exc) == "DEPTH_LIMIT_EXCEEDED" else "INVALID_INPUT"
        raise DeepevalBindingError(code, "DEEPEVAL_BINDING_JSON_INVALID", "/bindings") from None
    if type(pack) is not dict:
        raise DeepevalBindingError("INVALID_INPUT", "DEEPEVAL_BINDING_OBJECT_REQUIRED", "/bindings")
    return cast(dict[str, object], pack)


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


def audit_deepeval_bound_file(
    path: Path, bindings_path: Path, *, version: str
) -> dict[str, object]:
    """Validate one reusable owner pack and map a pinned TestRun's accounted facts."""
    if version != "4.2.8":
        raise DeepevalBindingError(
            "UNSUPPORTED_FORMAT", "DEEPEVAL_BINDING_VERSION_UNSUPPORTED", "/version"
        )
    pack = _read_pack(bindings_path)
    _validate_document(
        pack,
        "deepeval_metric_bindings.v1",
        "INVALID_INPUT",
        "DEEPEVAL_BINDING_SCHEMA_INVALID",
        "/bindings",
    )
    try:
        accounting = audit_deepeval_file(path, version=version)
    except DeepevalFileError as exc:
        raise DeepevalBindingError(exc.code, exc.reason, exc.location) from None
    result = bind_deepeval_accounting(accounting, pack)
    _validate_document(
        result, "evidence_result.v1", "INTERNAL_ERROR", "DEEPEVAL_BINDING_RESULT_INVALID", "/result"
    )
    return result
