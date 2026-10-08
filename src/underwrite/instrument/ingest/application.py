"""Trusted packaged ingestion and stable diagnostics shared by shell consumers."""

from __future__ import annotations

from importlib.resources import as_file, files

from underwrite_core.canonical import CanonicalizationError

from underwrite.instrument.ingest.codecs.deepeval import DeepevalPayloadError
from underwrite.instrument.ingest.codecs.evidence_bundle import EvidenceBundlePayloadError
from underwrite.instrument.ingest.codecs.inspect import InspectPayloadError
from underwrite.instrument.ingest.codecs.langfuse import LangfusePayloadError
from underwrite.instrument.ingest.codecs.openinference import OpenInferencePayloadError
from underwrite.instrument.ingest.codecs.otlp_json import OtlpJsonPayloadError
from underwrite.instrument.ingest.codecs.promptfoo import PromptfooPayloadError
from underwrite.instrument.ingest.formats import CodecConfigurationError, trusted_codecs
from underwrite.instrument.ingest.project import ProjectionError, SourceError
from underwrite.instrument.ingest.schema import ObservationValidationError, SchemaConfigurationError
from underwrite.instrument.ingest.transport import (
    DecodeError,
    Ingestor,
    TransportError,
    UnsupportedFormat,
)

DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_DEPTH = 64
NEXT_ACTIONS = {
    "SCHEMA_CONFIGURATION_ERROR": "REINSTALL_PACKAGE",
    "SOURCE_READ_FAILED": "PROVIDE_REGULAR_LOCAL_FILE",
    "USAGE_ERROR": "CORRECT_ARGUMENTS",
    "INTERNAL_ERROR": "REPORT_INTERNAL_ERROR",
    "UNSUPPORTED_PLATFORM": "USE_SUPPORTED_PLATFORM",
}


class UsageError(ValueError):
    """Invalid invocation without reflecting arbitrary input text."""


def packaged_ingestor(*, max_bytes: int, max_depth: int) -> Ingestor:
    """Eagerly load only the installed trusted resource, never a CWD schema."""
    resource = files("underwrite").joinpath("_contracts", "observation.v1.schema.json")
    try:
        with as_file(resource) as path:
            return Ingestor(path, trusted_codecs(), max_bytes=max_bytes, max_depth=max_depth)
    except OSError as exc:
        raise SchemaConfigurationError("INVALID_OBSERVATION_SCHEMA") from exc


# Values below are adapter-owned public reasons, never arbitrary exception text.
DIAGNOSTICS: dict[type[Exception], tuple[str, int, dict[str, str] | str]] = {
    UsageError: (
        "USAGE_ERROR",
        2,
        {
            "INVALID_ARGUMENTS_SEE_HELP": "INVALID_ARGUMENTS_SEE_HELP",
            "BODY_TRANSPORT_LOCATOR_REQUIRED": "BODY_TRANSPORT_LOCATOR_REQUIRED",
            "TRANSPORT_LOCATOR_REQUIRES_BODY": "TRANSPORT_LOCATOR_REQUIRES_BODY",
        },
    ),
    UnsupportedFormat: ("UNSUPPORTED_FORMAT", 1, {"UNSUPPORTED_FORMAT": "UNSUPPORTED_FORMAT"}),
    DecodeError: (
        "INVALID_INPUT",
        1,
        {
            "INVALID_JSON_INPUT": "INVALID_JSON_INPUT",
            "DUPLICATE_JSON_KEY": "DUPLICATE_JSON_KEY",
            "NON_FINITE_JSON_NUMBER": "NON_FINITE_JSON_NUMBER",
            "DEPTH_LIMIT_EXCEEDED": "DEPTH_LIMIT_EXCEEDED",
        },
    ),
    TransportError: (
        "INVALID_INPUT",
        1,
        {
            "SOURCE_READ_FAILED": "SOURCE_READ_FAILED",
            "SOURCE_NOT_REGULAR_FILE": "SOURCE_NOT_REGULAR_FILE",
            "SOURCE_NONBLOCKING_UNAVAILABLE": "SOURCE_NONBLOCKING_UNAVAILABLE",
            "BYTE_LIMIT_EXCEEDED": "BYTE_LIMIT_EXCEEDED",
        },
    ),
    SourceError: (
        "INVALID_SOURCE",
        1,
        {
            "INVALID_PROJECTION": "REFERENCE_MUST_BE_TEXT",
            "EMPTY_REFERENCE": "EMPTY_REFERENCE",
            "NON_NFC_REFERENCE": "NON_NFC_REFERENCE",
            "INVALID_REFERENCE_UNICODE": "INVALID_REFERENCE_UNICODE",
        },
    ),
    ProjectionError: (
        "INVALID_INPUT",
        1,
        {
            "INVALID_PROJECTION": "INVALID_PROJECTION",
            "NON_STRING_KEY": "NON_STRING_KEY",
            "NFC_KEY_COLLISION": "NFC_KEY_COLLISION",
        },
    ),
    ObservationValidationError: (
        "INVALID_OBSERVATION",
        1,
        {"INVALID_OBSERVATION": "INVALID_OBSERVATION"},
    ),
    SchemaConfigurationError: (
        "SCHEMA_CONFIGURATION_ERROR",
        2,
        {
            "INVALID_OBSERVATION_SCHEMA": "INVALID_OBSERVATION_SCHEMA",
            "WRONG_OBSERVATION_CONTRACT": "WRONG_OBSERVATION_CONTRACT",
            "NESTED_SCHEMA_RESOURCE": "NESTED_SCHEMA_RESOURCE",
            "NON_LOCAL_SCHEMA_REFERENCE": "NON_LOCAL_SCHEMA_REFERENCE",
            "OBSERVATION_SCHEMA_EVALUATION_FAILED": "OBSERVATION_SCHEMA_EVALUATION_FAILED",
        },
    ),
    CodecConfigurationError: (
        "SCHEMA_CONFIGURATION_ERROR",
        2,
        {"DUPLICATE_CODEC_SELECTOR": "DUPLICATE_CODEC_SELECTOR"},
    ),
    EvidenceBundlePayloadError: ("INVALID_INPUT", 1, "MALFORMED_EVIDENCE_BUNDLE_PAYLOAD"),
    DeepevalPayloadError: ("INVALID_INPUT", 1, "MALFORMED_DEEPEVAL_PAYLOAD"),
    InspectPayloadError: ("INVALID_INPUT", 1, "MALFORMED_INSPECT_PAYLOAD"),
    LangfusePayloadError: ("INVALID_INPUT", 1, "MALFORMED_LANGFUSE_PAYLOAD"),
    OpenInferencePayloadError: ("INVALID_INPUT", 1, "MALFORMED_OPENINFERENCE_PAYLOAD"),
    OtlpJsonPayloadError: ("INVALID_INPUT", 1, "MALFORMED_OTLP_JSON_PAYLOAD"),
    PromptfooPayloadError: ("INVALID_INPUT", 1, "MALFORMED_PROMPTFOO_PAYLOAD"),
}


def instrument_diagnostic(error: Exception, command: str) -> dict[str, object]:
    code, exit_code, reasons = DIAGNOSTICS[type(error)]
    reason = reasons if isinstance(reasons, str) else reasons[error.args[0]]
    if reason in {"BYTE_LIMIT_EXCEEDED", "DEPTH_LIMIT_EXCEEDED"}:
        code = "INPUT_LIMIT_EXCEEDED"
    elif reason in {
        "SOURCE_READ_FAILED",
        "SOURCE_NOT_REGULAR_FILE",
        "SOURCE_NONBLOCKING_UNAVAILABLE",
    }:
        code, exit_code = "SOURCE_READ_FAILED", 2
    elif isinstance(error, ProjectionError) and isinstance(error.__cause__, CanonicalizationError):
        if error.__cause__.args == ("INT_OUT_OF_SAFE_RANGE",):
            code, reason = "CANONICAL_NUMBER_INCOMPATIBLE", "INT_OUT_OF_SAFE_RANGE"
    return {
        "schema": "instrument_error.v1",
        "command": command,
        "code": code,
        "reason": reason,
        "exit_code": exit_code,
    }
