"""Account for one DeepEval file with automatic or explicitly pinned local rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from underwrite_core.canonical import content_digest

from underwrite.instrument.evidence.deepeval_accounting import (
    RULE_PROFILE,
    RULE_PROFILE_V2,
    DeepevalAccountingError,
    account_deepeval,
)
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DIAGNOSTICS,
    direct_file_read_error,
    instrument_diagnostic,
    packaged_ingestor,
)
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import TransportError, read_local_file

SOURCE = ("deepeval.test-run", "4.1.1")


@dataclass(frozen=True)
class DeepevalProfile:
    """One allowlisted producer version and its owned result contracts."""

    format_version: str
    rule_profile: str
    accounting_schema: str
    record_schema: str


PROFILES = MappingProxyType(
    {
        "4.1.1": DeepevalProfile(
            "4.1.1", RULE_PROFILE, "deepeval_accounting.v1", "deepeval_record.v1"
        ),
        "4.2.8": DeepevalProfile(
            "4.2.8", RULE_PROFILE_V2, "deepeval_accounting.v2", "deepeval_record.v2"
        ),
    }
)


class DeepevalFileError(ValueError):
    """A code-owned acquisition or accounting refusal without source path text."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def profile_for(version: str) -> DeepevalProfile:
    """Refuse unknown selection before any input acquisition or storage."""
    if type(version) is not str or version not in PROFILES:
        raise DeepevalFileError("UNSUPPORTED_FORMAT", "UNSUPPORTED_DEEPEVAL_VERSION")
    return PROFILES[version]


def audit_deepeval_file(path: Path, *, version: str | None = None) -> dict[str, object]:
    """Read once, normalize, and account without running a producer or evaluator."""
    if version is None:
        from underwrite.instrument.ingest.deepeval_compat import audit_deepeval_compatible_file

        return audit_deepeval_compatible_file(path)
    profile = profile_for(version)
    try:
        observation = (
            packaged_ingestor(max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH)
            .file(path, Source(SOURCE[0], profile.format_version, None))
            .observation
        )
    except tuple(DIAGNOSTICS) as exc:
        raise _instrument_error(exc) from None
    return _account(observation, profile)


def _instrument_error(exc: Exception) -> DeepevalFileError:
    diagnostic = instrument_diagnostic(exc, "ingest")
    code = cast(str, diagnostic["code"])
    if code not in {
        "SOURCE_READ_FAILED",
        "INPUT_LIMIT_EXCEEDED",
        "UNSUPPORTED_FORMAT",
        "SCHEMA_CONFIGURATION_ERROR",
    }:
        code = "INVALID_INPUT"
    location = "" if code == "SCHEMA_CONFIGURATION_ERROR" else "/file"
    return DeepevalFileError(code, cast(str, diagnostic["reason"]), location)


def _account(observation: dict[str, object], profile: DeepevalProfile) -> dict[str, object]:
    try:
        counted = account_deepeval(observation["payload"], rule_profile=profile.rule_profile)
    except DeepevalAccountingError as exc:
        raise DeepevalFileError(exc.code, exc.reason, exc.location) from None
    return {
        "schema": profile.accounting_schema,
        "source": {
            "format": SOURCE[0],
            "format_version": profile.format_version,
            "claims_verified": False,
        },
        "raw_hash": cast(dict[str, object], observation["raw"])["hash"],
        "observation_digest": content_digest(observation),
        **counted,
    }


def read_deepeval_file(path: Path) -> bytes:
    """Capture one complete bounded file without projecting or reopening it."""
    try:
        return read_local_file(path, DEFAULT_MAX_BYTES)
    except TransportError as exc:
        code, reason = direct_file_read_error(exc)
        raise DeepevalFileError(code, reason, "/file") from None


def audit_deepeval_bytes(raw: bytes, *, version: str | None = None) -> dict[str, object]:
    """Account for bounded original bytes using the selected local reader."""
    if version is None:
        from underwrite.instrument.ingest.deepeval_compat import audit_deepeval_compatible_bytes

        return audit_deepeval_compatible_bytes(raw)
    profile = profile_for(version)
    try:
        result, _ = packaged_ingestor(
            max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH
        ).buffered_projection(raw, "deepeval-file", Source(SOURCE[0], profile.format_version, None))
    except tuple(DIAGNOSTICS) as exc:
        raise _instrument_error(exc) from None
    return _account(result.observation, profile)
