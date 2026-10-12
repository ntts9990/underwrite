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
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DIAGNOSTICS,
    instrument_diagnostic,
    packaged_ingestor,
)
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor, TransportError, read_local_file

SOURCES = MappingProxyType(
    {
        "observations": ("langfuse.observations-v2", "4.35.0"),
        "scores": ("langfuse.scores", "4.35.0"),
    }
)


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


def _capture(
    ingestor: Ingestor, path: Path, role: str
) -> tuple[dict[str, object], dict[str, object]]:
    location = "/" + role
    try:
        raw = read_local_file(path, DEFAULT_MAX_BYTES)
    except TransportError as exc:
        reason = str(exc)
        if reason == "BYTE_LIMIT_EXCEEDED":
            code = "INPUT_LIMIT_EXCEEDED"
        elif reason == "UNSUPPORTED_PLATFORM":
            code = "UNSUPPORTED_PLATFORM"
        else:
            code = "SOURCE_READ_FAILED"
            if reason != "SOURCE_NOT_REGULAR_FILE":
                reason = "SOURCE_READ_FAILED"
        raise LangfuseFileError(code, reason, location) from None
    try:
        result, projection = ingestor.buffered_projection(raw, role, Source(*SOURCES[role], None))
    except tuple(DIAGNOSTICS) as exc:
        raise _instrument_error(exc, location) from None
    observation = result.observation
    identity = {
        "format": SOURCES[role][0],
        "format_version": SOURCES[role][1],
        "raw_hash": cast(dict[str, object], observation["raw"])["hash"],
        "observation_digest": content_digest(observation),
        "claims_verified": False,
    }
    return identity, projection.payload


def audit_langfuse_files(observations_file: Path, scores_file: Path) -> dict[str, object]:
    """Read each source once and keep original IDs separate from normalized observations."""
    try:
        ingestor = packaged_ingestor(max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH)
    except tuple(DIAGNOSTICS) as exc:
        raise _instrument_error(exc, "") from None
    observation_identity, observations = _capture(ingestor, observations_file, "observations")
    score_identity, scores = _capture(ingestor, scores_file, "scores")
    try:
        counted = account_langfuse(observations["observations"], scores["scores"])
    except LangfuseAccountingError as exc:
        raise LangfuseFileError(exc.code, exc.reason, exc.location) from None
    return {
        "schema": "langfuse_accounting.v1",
        "sources": {"observations": observation_identity, "scores": score_identity},
        **counted,
    }
