"""Associate two bounded Langfuse exports using original opaque identifiers."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import cast

from underwrite_core.canonical import content_digest

from underwrite.instrument.evidence.langfuse_accounting import (
    LangfuseAccountingError,
    account_langfuse,
)
from underwrite.instrument.evidence.langfuse_cli import (
    ACCOUNTING_SCHEMA,
    CLI_PROFILE,
    ROW_INDEX_SCHEMA,
)
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA as AUTO_ACCOUNTING_SCHEMA,
)
from underwrite.instrument.evidence.langfuse_compat import AUTO_PROFILE
from underwrite.instrument.evidence.langfuse_compat import (
    ROW_INDEX_SCHEMA as AUTO_ROW_INDEX_SCHEMA,
)
from underwrite.instrument.evidence.langfuse_row_index import index_langfuse_accounting
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DIAGNOSTICS,
    direct_file_read_error,
    instrument_diagnostic,
    packaged_ingestor,
)
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import Ingestor, TransportError, read_local_file

SOURCES = MappingProxyType(
    {
        "observations": ("langfuse.observations-v2", "4.35.0"),
        "scores": ("langfuse.scores", "4.35.0"),
    }
)
BLOB_PROFILE = "blob-4.35.0"
OUTPUT_SCHEMAS = MappingProxyType(
    {
        BLOB_PROFILE: MappingProxyType(
            {"accounting": "langfuse_accounting.v1", "row-index": "langfuse_row_index.v1"}
        ),
        CLI_PROFILE: MappingProxyType(
            {"accounting": ACCOUNTING_SCHEMA, "row-index": ROW_INDEX_SCHEMA}
        ),
        AUTO_PROFILE: MappingProxyType(
            {"accounting": AUTO_ACCOUNTING_SCHEMA, "row-index": AUTO_ROW_INDEX_SCHEMA}
        ),
    }
)


def output_schema(profile: str | None, view: str) -> str:
    """Resolve a selected Langfuse source shape and view to its exact result contract."""
    try:
        return OUTPUT_SCHEMAS[AUTO_PROFILE if profile is None else profile][view]
    except KeyError:
        raise ValueError("UNSUPPORTED_LANGFUSE_PROFILE_OR_VIEW") from None


class LangfuseFileError(ValueError):
    """A trusted diagnostic with structural locations rather than source paths."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _instrument_error(exc: Exception, location: str) -> LangfuseFileError:
    diagnostic = instrument_diagnostic(exc, "ingest")
    code = cast(str, diagnostic["code"])
    if code not in {
        "SOURCE_READ_FAILED",
        "INPUT_LIMIT_EXCEEDED",
        "UNSUPPORTED_FORMAT",
        "SCHEMA_CONFIGURATION_ERROR",
    }:
        code = "INVALID_INPUT"
    return LangfuseFileError(
        code,
        cast(str, diagnostic["reason"]),
        "" if code == "SCHEMA_CONFIGURATION_ERROR" else location,
    )


def read_langfuse_file(path: Path, role: str) -> bytes:
    """Read one complete bounded export, with a role-only diagnostic location."""
    if role not in SOURCES:
        raise ValueError("unknown Langfuse source role")
    location = "/" + role
    try:
        return read_local_file(path, DEFAULT_MAX_BYTES)
    except TransportError as exc:
        code, reason = direct_file_read_error(exc)
        raise LangfuseFileError(code, reason, location) from None


def _capture(
    ingestor: Ingestor, raw: bytes, role: str
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        result, projection = ingestor.buffered_projection(raw, role, Source(*SOURCES[role], None))
    except tuple(DIAGNOSTICS) as exc:
        raise _instrument_error(exc, "/" + role) from None
    observation = result.observation
    identity = {
        "format": SOURCES[role][0],
        "format_version": SOURCES[role][1],
        "raw_hash": cast(dict[str, object], observation["raw"])["hash"],
        "observation_digest": content_digest(observation),
        "claims_verified": False,
    }
    return identity, projection.payload


def audit_langfuse_files(
    observations_file: Path, scores_file: Path, *, profile: str | None = None
) -> dict[str, object]:
    """Read each source once and keep original IDs separate from normalized observations."""
    if profile is None or profile == AUTO_PROFILE:
        from underwrite.instrument.ingest.langfuse_compat import audit_langfuse_compatible_files

        return audit_langfuse_compatible_files(observations_file, scores_file)
    if profile == CLI_PROFILE:
        from underwrite.instrument.ingest.langfuse_cli import audit_langfuse_cli_files

        return audit_langfuse_cli_files(observations_file, scores_file)
    if profile != BLOB_PROFILE:
        raise LangfuseFileError("UNSUPPORTED_FORMAT", "LANGFUSE_PROFILE_UNSUPPORTED", "/profile")
    try:
        ingestor = packaged_ingestor(max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH)
    except tuple(DIAGNOSTICS) as exc:
        raise _instrument_error(exc, "") from None
    observation_identity, observations = _capture(
        ingestor, read_langfuse_file(observations_file, "observations"), "observations"
    )
    score_identity, scores = _capture(ingestor, read_langfuse_file(scores_file, "scores"), "scores")
    return _account(observation_identity, observations, score_identity, scores)


def audit_langfuse_row_index_files(
    observations_file: Path, scores_file: Path, *, profile: str | None = None
) -> dict[str, object]:
    """Return the checked content-minimized view of the same two selected files."""
    if profile is None or profile == AUTO_PROFILE:
        from underwrite.instrument.ingest.langfuse_compat import audit_langfuse_compatible_files

        return audit_langfuse_compatible_files(observations_file, scores_file, view="row-index")
    if profile == CLI_PROFILE:
        from underwrite.instrument.ingest.langfuse_cli import audit_langfuse_cli_files

        return audit_langfuse_cli_files(observations_file, scores_file, view="row-index")
    accounting = audit_langfuse_files(observations_file, scores_file, profile=profile)
    try:
        packaged_validator("langfuse_accounting.v1").validate(accounting)  # pyright: ignore[reportUnknownMemberType]
        result = index_langfuse_accounting(accounting)
        packaged_validator("langfuse_row_index.v1").validate(result)  # pyright: ignore[reportUnknownMemberType]
    except SchemaConfigurationError:
        raise LangfuseFileError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT") from None
    except Exception:
        raise LangfuseFileError("INTERNAL_ERROR", "LANGFUSE_ROW_INDEX_INVALID") from None
    return result


def audit_langfuse_bytes(
    observations: bytes, scores: bytes, *, profile: str | None = None
) -> dict[str, object]:
    """Account for the exact two supplied buffers without reopening their sources."""
    if profile is None or profile == AUTO_PROFILE:
        from underwrite.instrument.ingest.langfuse_compat import audit_langfuse_compatible_bytes

        return audit_langfuse_compatible_bytes(observations, scores)
    if profile == CLI_PROFILE:
        from underwrite.instrument.ingest.langfuse_cli import audit_langfuse_cli_bytes

        return audit_langfuse_cli_bytes(observations, scores)
    if profile != BLOB_PROFILE:
        raise LangfuseFileError("UNSUPPORTED_FORMAT", "LANGFUSE_PROFILE_UNSUPPORTED", "/profile")
    try:
        ingestor = packaged_ingestor(max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH)
    except tuple(DIAGNOSTICS) as exc:
        raise _instrument_error(exc, "") from None
    observation_identity, observation_payload = _capture(ingestor, observations, "observations")
    score_identity, score_payload = _capture(ingestor, scores, "scores")
    return _account(observation_identity, observation_payload, score_identity, score_payload)


def _account(
    observation_identity: dict[str, object],
    observations: dict[str, object],
    score_identity: dict[str, object],
    scores: dict[str, object],
) -> dict[str, object]:
    try:
        counted = account_langfuse(observations["observations"], scores["scores"])
    except LangfuseAccountingError as exc:
        raise LangfuseFileError(exc.code, exc.reason, exc.location) from None
    return {
        "schema": "langfuse_accounting.v1",
        "sources": {"observations": observation_identity, "scores": score_identity},
        **counted,
    }
