"""Read one bounded DeepEval TestRun with a selected version-neutral local policy."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.evidence.deepeval_accounting import DeepevalAccountingError
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_SCHEMA,
    TRACE_SCHEMA,
    account_deepeval_compatible,
    index_deepeval_compatible,
)
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    instrument_diagnostic,
)
from underwrite.instrument.ingest.deepeval_accounting import DeepevalFileError, read_deepeval_file
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import DecodeError, decode_json

type View = Literal["accounting", "trace-index"]


def _validator(view: View) -> Draft202012Validator:
    if view not in ("accounting", "trace-index"):
        raise DeepevalFileError("UNSUPPORTED_FORMAT", "DEEPEVAL_AUTO_VIEW_UNSUPPORTED", "/view")
    try:
        return packaged_validator(ACCOUNTING_SCHEMA if view == "accounting" else TRACE_SCHEMA)
    except SchemaConfigurationError:
        raise DeepevalFileError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT") from None


def _audit(raw: bytes, view: View, validator: Draft202012Validator) -> dict[str, object]:
    if len(raw) > DEFAULT_MAX_BYTES:
        raise DeepevalFileError("INPUT_LIMIT_EXCEEDED", "BYTE_LIMIT_EXCEEDED", "/file")
    try:
        payload = decode_json(raw, DEFAULT_MAX_DEPTH)
    except DecodeError as exc:
        diagnostic = instrument_diagnostic(exc, "ingest")
        raise DeepevalFileError(
            cast(str, diagnostic["code"]), cast(str, diagnostic["reason"]), "/file"
        ) from None
    try:
        counted = (
            account_deepeval_compatible(payload)
            if view == "accounting"
            else index_deepeval_compatible(payload)
        )
    except DeepevalAccountingError as exc:
        raise DeepevalFileError(exc.code, exc.reason, exc.location) from None
    report = {
        "schema": ACCOUNTING_SCHEMA if view == "accounting" else TRACE_SCHEMA,
        "raw_hash": digest_bytes(raw),
        **counted,
    }
    try:
        validator.validate(report)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError:
        raise DeepevalFileError("INTERNAL_ERROR", "DEEPEVAL_AUTO_RESULT_INVALID") from None
    except Exception:
        raise DeepevalFileError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT") from None
    return report


def audit_deepeval_compatible_bytes(
    raw: bytes, *, view: View = "accounting"
) -> dict[str, object]:
    """Interpret only supplied serialized rows, preserving the original byte hash."""
    return _audit(raw, view, _validator(view))


def audit_deepeval_compatible_file(
    path: Path, *, view: View = "accounting"
) -> dict[str, object]:
    """Choose the packaged contract before acquiring the one local source."""
    validator = _validator(view)
    return _audit(read_deepeval_file(path), view, validator)
