"""Audit two original Langfuse buffers with one selected local reader family."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.evidence.langfuse_accounting import LangfuseAccountingError
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    ROW_INDEX_SCHEMA,
    account_langfuse_compatible,
    index_langfuse_compatible,
)
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    instrument_diagnostic,
)
from underwrite.instrument.ingest.langfuse_accounting import (
    LangfuseFileError,
    read_langfuse_file,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import DecodeError, decode_json

type View = Literal["accounting", "row-index"]


def _validators(view: View) -> tuple[Draft202012Validator, Draft202012Validator | None]:
    if view not in ("accounting", "row-index"):
        raise LangfuseFileError("UNSUPPORTED_FORMAT", "LANGFUSE_AUTO_VIEW_UNSUPPORTED", "/view")
    try:
        full = packaged_validator(ACCOUNTING_SCHEMA)
        index = packaged_validator(ROW_INDEX_SCHEMA) if view == "row-index" else None
    except SchemaConfigurationError:
        raise LangfuseFileError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT") from None
    return full, index


def _decoded(raw: bytes, role: str) -> object:
    location = "/" + role
    if len(raw) > DEFAULT_MAX_BYTES:
        raise LangfuseFileError("INPUT_LIMIT_EXCEEDED", "BYTE_LIMIT_EXCEEDED", location)
    try:
        return decode_json(raw, DEFAULT_MAX_DEPTH)
    except DecodeError as exc:
        diagnostic = instrument_diagnostic(exc, "ingest")
        code = cast(str, diagnostic["code"])
        if code != "INPUT_LIMIT_EXCEEDED":
            code = "INVALID_INPUT"
        raise LangfuseFileError(
            code,
            cast(str, diagnostic["reason"]),
            location,
        ) from None


def _checked(validator: Draft202012Validator, report: dict[str, object]) -> None:
    try:
        validator.validate(report)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError:
        raise LangfuseFileError("INTERNAL_ERROR", "LANGFUSE_AUTO_RESULT_INVALID") from None
    except Exception:
        raise LangfuseFileError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT") from None


def _audit(
    observations: bytes,
    scores: bytes,
    view: View,
    full_validator: Draft202012Validator,
    index_validator: Draft202012Validator | None,
) -> dict[str, object]:
    observation_document = _decoded(observations, "observations")
    score_document = _decoded(scores, "scores")
    try:
        counted = account_langfuse_compatible(observation_document, score_document)
    except LangfuseAccountingError as exc:
        raise LangfuseFileError(exc.code, exc.reason, exc.location) from None
    except Exception:
        raise LangfuseFileError("INTERNAL_ERROR", "LANGFUSE_AUTO_RESULT_INVALID") from None
    sources = cast(dict[str, dict[str, object]], counted["sources"])
    report = {
        "schema": ACCOUNTING_SCHEMA,
        **counted,
        "sources": {
            "observations": {**sources["observations"], "raw_hash": digest_bytes(observations)},
            "scores": {**sources["scores"], "raw_hash": digest_bytes(scores)},
        },
    }
    _checked(full_validator, report)
    if view == "accounting":
        return report
    try:
        selected = {"schema": ROW_INDEX_SCHEMA, **index_langfuse_compatible(report)}
    except LangfuseAccountingError as exc:
        raise LangfuseFileError(exc.code, exc.reason, exc.location) from None
    except Exception:
        raise LangfuseFileError("INTERNAL_ERROR", "LANGFUSE_ROW_INDEX_INVALID") from None
    assert index_validator is not None
    _checked(index_validator, selected)
    return selected


def audit_langfuse_compatible_bytes(
    observations: bytes, scores: bytes, *, view: View = "accounting"
) -> dict[str, object]:
    """Interpret one coherent captured pair without a producer-release claim."""
    full, index = _validators(view)
    return _audit(observations, scores, view, full, index)


def audit_langfuse_compatible_files(
    observations_file: Path, scores_file: Path, *, view: View = "accounting"
) -> dict[str, object]:
    """Read each local source once after selecting the fixed auto contract."""
    full, index = _validators(view)
    observations = read_langfuse_file(observations_file, "observations")
    scores = read_langfuse_file(scores_file, "scores")
    return _audit(observations, scores, view, full, index)
