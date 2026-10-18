"""Audit exact local Langfuse CLI 1.2.4 captures without network access."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.evidence.langfuse_accounting import LangfuseAccountingError
from underwrite.instrument.evidence.langfuse_cli import (
    CLI_PROFILE,
    SOURCE_FORMATS,
    SOURCE_VERSION,
    account_langfuse_cli,
    index_langfuse_cli_accounting,
)
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    instrument_diagnostic,
)
from underwrite.instrument.ingest.langfuse_accounting import (
    LangfuseFileError,
    output_schema,
    read_langfuse_file,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import DecodeError, decode_json

type View = Literal["accounting", "row-index"]


def _validators(view: View) -> tuple[Draft202012Validator, Draft202012Validator | None]:
    if view not in ("accounting", "row-index"):
        raise LangfuseFileError("UNSUPPORTED_FORMAT", "LANGFUSE_CLI_VIEW_UNSUPPORTED", "/view")
    try:
        full = packaged_validator(output_schema(CLI_PROFILE, "accounting"))
        index = (
            packaged_validator(output_schema(CLI_PROFILE, "row-index"))
            if view == "row-index"
            else None
        )
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
        raise LangfuseFileError(code, cast(str, diagnostic["reason"]), location) from None


def _identity(role: str, raw: bytes) -> dict[str, object]:
    return {
        "format": SOURCE_FORMATS[role],
        "format_version": SOURCE_VERSION,
        "raw_hash": digest_bytes(raw),
        "claims_verified": False,
    }


def _check_output(validator: Draft202012Validator, report: dict[str, object]) -> None:
    try:
        validator.validate(report)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError:
        raise LangfuseFileError("INTERNAL_ERROR", "LANGFUSE_CLI_RESULT_INVALID") from None
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
        counted = account_langfuse_cli(observation_document, score_document)
    except LangfuseAccountingError as exc:
        raise LangfuseFileError(exc.code, exc.reason, exc.location) from None
    report = {
        "schema": output_schema(CLI_PROFILE, "accounting"),
        "sources": {
            "observations": _identity("observations", observations),
            "scores": _identity("scores", scores),
        },
        **counted,
    }
    _check_output(full_validator, report)
    if view == "accounting":
        return report
    try:
        selected = index_langfuse_cli_accounting(report)
    except LangfuseAccountingError as exc:
        raise LangfuseFileError(exc.code, exc.reason, exc.location) from None
    assert index_validator is not None
    _check_output(index_validator, selected)
    return selected


def audit_langfuse_cli_bytes(
    observations: bytes, scores: bytes, *, view: View = "accounting"
) -> dict[str, object]:
    """Account for the two original buffers with the pinned native CLI semantics."""
    full, index = _validators(view)
    return _audit(observations, scores, view, full, index)


def audit_langfuse_cli_files(
    observations_file: Path, scores_file: Path, *, view: View = "accounting"
) -> dict[str, object]:
    """Read each selected regular file once; never fetch from the producer."""
    full, index = _validators(view)
    observations = read_langfuse_file(observations_file, "observations")
    scores = read_langfuse_file(scores_file, "scores")
    return _audit(observations, scores, view, full, index)
