"""Expected diagnostic examples shared across library and CLI boundary tests."""

from underwrite.instrument.ingest.application import UsageError
from underwrite.instrument.ingest.codecs.deepeval import DeepevalPayloadError
from underwrite.instrument.ingest.codecs.evidence_bundle import EvidenceBundlePayloadError
from underwrite.instrument.ingest.codecs.inspect import InspectPayloadError
from underwrite.instrument.ingest.codecs.langfuse import LangfusePayloadError
from underwrite.instrument.ingest.codecs.openinference import OpenInferencePayloadError
from underwrite.instrument.ingest.codecs.otlp_json import OtlpJsonPayloadError
from underwrite.instrument.ingest.codecs.promptfoo import PromptfooPayloadError
from underwrite.instrument.ingest.formats import CodecConfigurationError
from underwrite.instrument.ingest.project import ProjectionError, SourceError
from underwrite.instrument.ingest.schema import ObservationValidationError, SchemaConfigurationError
from underwrite.instrument.ingest.transport import DecodeError, TransportError, UnsupportedFormat

ERROR_CASES: list[tuple[Exception, str, str, int]] = [
    (UsageError("INVALID_ARGUMENTS_SEE_HELP"), "USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP", 2),
    (
        UsageError("BODY_TRANSPORT_LOCATOR_REQUIRED"),
        "USAGE_ERROR",
        "BODY_TRANSPORT_LOCATOR_REQUIRED",
        2,
    ),
    (
        UsageError("TRANSPORT_LOCATOR_REQUIRES_BODY"),
        "USAGE_ERROR",
        "TRANSPORT_LOCATOR_REQUIRES_BODY",
        2,
    ),
    (UnsupportedFormat("UNSUPPORTED_FORMAT"), "UNSUPPORTED_FORMAT", "UNSUPPORTED_FORMAT", 1),
    (DecodeError("INVALID_JSON_INPUT"), "INVALID_INPUT", "INVALID_JSON_INPUT", 1),
    (DecodeError("DUPLICATE_JSON_KEY"), "INVALID_INPUT", "DUPLICATE_JSON_KEY", 1),
    (DecodeError("NON_FINITE_JSON_NUMBER"), "INVALID_INPUT", "NON_FINITE_JSON_NUMBER", 1),
    (DecodeError("DEPTH_LIMIT_EXCEEDED"), "INPUT_LIMIT_EXCEEDED", "DEPTH_LIMIT_EXCEEDED", 1),
    (TransportError("BYTE_LIMIT_EXCEEDED"), "INPUT_LIMIT_EXCEEDED", "BYTE_LIMIT_EXCEEDED", 1),
    (TransportError("SOURCE_READ_FAILED"), "SOURCE_READ_FAILED", "SOURCE_READ_FAILED", 2),
    (TransportError("SOURCE_NOT_REGULAR_FILE"), "SOURCE_READ_FAILED", "SOURCE_NOT_REGULAR_FILE", 2),
    (
        TransportError("SOURCE_NONBLOCKING_UNAVAILABLE"),
        "SOURCE_READ_FAILED",
        "SOURCE_NONBLOCKING_UNAVAILABLE",
        2,
    ),
    (SourceError("EMPTY_REFERENCE"), "INVALID_SOURCE", "EMPTY_REFERENCE", 1),
    (SourceError("NON_NFC_REFERENCE"), "INVALID_SOURCE", "NON_NFC_REFERENCE", 1),
    (ProjectionError("NFC_KEY_COLLISION"), "INVALID_INPUT", "NFC_KEY_COLLISION", 1),
    (SourceError("INVALID_PROJECTION"), "INVALID_SOURCE", "REFERENCE_MUST_BE_TEXT", 1),
    (
        SourceError("INVALID_REFERENCE_UNICODE"),
        "INVALID_SOURCE",
        "INVALID_REFERENCE_UNICODE",
        1,
    ),
    (ProjectionError("NON_STRING_KEY"), "INVALID_INPUT", "NON_STRING_KEY", 1),
    (ProjectionError("INVALID_PROJECTION"), "INVALID_INPUT", "INVALID_PROJECTION", 1),
    (
        ObservationValidationError("INVALID_OBSERVATION"),
        "INVALID_OBSERVATION",
        "INVALID_OBSERVATION",
        1,
    ),
    (
        CodecConfigurationError("DUPLICATE_CODEC_SELECTOR"),
        "SCHEMA_CONFIGURATION_ERROR",
        "DUPLICATE_CODEC_SELECTOR",
        2,
    ),
    (
        EvidenceBundlePayloadError("secret input"),
        "INVALID_INPUT",
        "MALFORMED_EVIDENCE_BUNDLE_PAYLOAD",
        1,
    ),
    (DeepevalPayloadError("secret input"), "INVALID_INPUT", "MALFORMED_DEEPEVAL_PAYLOAD", 1),
    (InspectPayloadError("secret input"), "INVALID_INPUT", "MALFORMED_INSPECT_PAYLOAD", 1),
    (LangfusePayloadError("secret input"), "INVALID_INPUT", "MALFORMED_LANGFUSE_PAYLOAD", 1),
    (
        OpenInferencePayloadError("secret input"),
        "INVALID_INPUT",
        "MALFORMED_OPENINFERENCE_PAYLOAD",
        1,
    ),
    (OtlpJsonPayloadError("secret input"), "INVALID_INPUT", "MALFORMED_OTLP_JSON_PAYLOAD", 1),
    (PromptfooPayloadError("secret input"), "INVALID_INPUT", "MALFORMED_PROMPTFOO_PAYLOAD", 1),
    *[
        (SchemaConfigurationError(reason), "SCHEMA_CONFIGURATION_ERROR", reason, 2)
        for reason in (
            "INVALID_OBSERVATION_SCHEMA",
            "WRONG_OBSERVATION_CONTRACT",
            "NESTED_SCHEMA_RESOURCE",
            "NON_LOCAL_SCHEMA_REFERENCE",
            "OBSERVATION_SCHEMA_EVALUATION_FAILED",
        )
    ],
]
