"""Promptfoo accounting over bounded manifest or explicit-file acquisition."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from underwrite_core.canonical import content_digest

from underwrite.instrument.evidence.accounting import (
    ROW_INDEX_LIMITATIONS,
    ROW_INDEX_SCOPE,
    AccountingError,
    RowIndex,
    audit_promptfoo,
    audit_promptfoo_with_rows,
)
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DIAGNOSTICS,
    instrument_diagnostic,
    packaged_ingestor,
)
from underwrite.instrument.ingest.codecs.promptfoo import SUPPORTED_VERSIONS
from underwrite.instrument.ingest.inspection import (
    InspectionError,
    inspect_artifact_with_projection,
)
from underwrite.instrument.ingest.project import Projection, Source
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import TransportError, read_local_file

SOURCE = ("promptfoo.eval-output", "0.123.0")
LIMITATIONS = [
    "PLANNED_DENOMINATOR_UNKNOWN",
    "CAUSE_UNKNOWN",
    "SOURCE_CLAIMS_UNVERIFIED",
    "NO_APPROVAL_AUTHORITY",
]


class AccountingCaseError(ValueError):
    """Bounded shell refusal; diagnostic text never includes source payload data."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        self.code = code
        self.reason = reason
        self.location = location
        super().__init__(reason)


def _inspection_error(error: InspectionError) -> AccountingCaseError:
    payload = error.payload
    code = cast(str, payload["code"])
    location = cast(str, payload["location"])
    if code == "UNSUPPORTED_FORMAT":
        return AccountingCaseError(
            "UNSUPPORTED_PROFILE", "UNSUPPORTED_PROMPTFOO_PROFILE", "/source"
        )
    if code == "INVALID_SOURCE":
        return AccountingCaseError("INVALID_INPUT", "INVALID_SOURCE", location)
    if code == "SCHEMA_CONFIGURATION_ERROR":
        return AccountingCaseError(code, "INVALID_PACKAGED_CONTRACT")
    if code in {
        "SOURCE_READ_FAILED",
        "INPUT_LIMIT_EXCEEDED",
        "INVALID_MANIFEST",
        "INVALID_ARTIFACT_PATH",
        "UNSUPPORTED_PLATFORM",
    }:
        return AccountingCaseError(code, code, location)
    return AccountingCaseError("INVALID_INPUT", "INVALID_INPUT", location)


def _trusted_artifact_diagnostic(report: dict[str, object]) -> tuple[str, str] | None:
    """Retain only one fixed registry reason from an artifact inspection finding."""
    diagnostics = report.get("diagnostics")
    if not isinstance(diagnostics, list):
        return None
    items = cast(list[object], diagnostics)
    if len(items) != 1:
        return None
    diagnostic = items[0]
    if not isinstance(diagnostic, dict):
        return None
    entry = cast(dict[str, object], diagnostic)
    code, reason, location = (
        entry.get("code"),
        entry.get("reason"),
        entry.get("location"),
    )
    if (
        type(code) is not str
        or type(reason) is not str
        or type(location) is not str
        or location != "/artifact"
    ):
        return None
    known_codes = {
        registered for registered, exit_code, _ in DIAGNOSTICS.values() if exit_code == 1
    }
    known_codes.add("INPUT_LIMIT_EXCEEDED")  # The registry maps bounded-input reasons to this code.
    known_reasons = {
        item
        for _, exit_code, reasons in DIAGNOSTICS.values()
        if exit_code == 1
        for item in (reasons.values() if isinstance(reasons, dict) else (reasons,))
    }
    return (reason, location) if code in known_codes and reason in known_reasons else None


def audit_case(path: Path) -> dict[str, object]:
    """Audit an admitted Promptfoo manifest from its hash-checked projection and identity."""
    try:
        report, projection = inspect_artifact_with_projection(
            path, allowed_sources=frozenset((SOURCE[0], version) for version in SUPPORTED_VERSIONS)
        )
    except InspectionError as exc:
        raise _inspection_error(exc) from exc
    if projection is None:
        if report["expected_hash_check"] == "mismatched":
            raise AccountingCaseError(
                "EXPECTED_HASH_MISMATCH", "EXPECTED_HASH_MISMATCH", "/artifact/expected_hash"
            )
        trusted = _trusted_artifact_diagnostic(report)
        if trusted is not None:
            reason, location = trusted
            raise AccountingCaseError("INVALID_INPUT", reason, location)
        raise AccountingCaseError("INVALID_INPUT", "INVALID_ARTIFACT", "/artifact")
    observation = report["observation"]
    if not isinstance(observation, dict):
        raise AccountingCaseError("INTERNAL_ERROR", "INTERNAL_ERROR")
    observation_digest = cast(dict[str, object], observation).get("digest")
    source = cast(dict[str, str], report["source"])
    return _account_projection(
        projection,
        report["raw_hash"],
        observation_digest,
        "/artifact",
        version=source["format_version"],
    )


def _account_projection(
    projection: Projection,
    raw_hash: object,
    observation_digest: object,
    location: str,
    *,
    version: str = SOURCE[1],
    row_index: bool = False,
) -> dict[str, object]:
    summary = projection.payload.get("results")
    if not isinstance(summary, dict):
        raise AccountingCaseError("INTERNAL_ERROR", "INTERNAL_ERROR")
    source = cast(dict[str, object], summary)
    classified: list[RowIndex] = []
    try:
        if row_index:
            counted, classified = audit_promptfoo_with_rows(
                source.get("stats"), source.get("results")
            )
        else:
            counted = audit_promptfoo(source.get("stats"), source.get("results"))
    except AccountingError as exc:
        if str(exc) == "ROW_LIMIT_EXCEEDED":
            raise AccountingCaseError(
                "COMPUTATION_LIMIT_EXCEEDED", "ROW_LIMIT_EXCEEDED", location
            ) from exc
        raise AccountingCaseError("INVALID_INPUT", str(exc), location) from exc
    if not row_index and not isinstance(observation_digest, str):
        raise AccountingCaseError("INTERNAL_ERROR", "INTERNAL_ERROR")
    if row_index:
        return {
            "schema": "promptfoo_row_index.v1",
            "source": {"format": SOURCE[0], "format_version": version},
            "raw_hash": raw_hash,
            "scope": dict(ROW_INDEX_SCOPE),
            **counted,
            "rows": classified,
            "verification": [],
            "limitations": list(ROW_INDEX_LIMITATIONS),
        }
    return {
        "schema": "promptfoo_accounting.v1",
        "source": {"format": SOURCE[0], "format_version": version},
        "raw_hash": raw_hash,
        "observation_digest": observation_digest,
        **counted,
        "limitations": list(LIMITATIONS),
    }


def read_promptfoo_file(path: Path) -> bytes:
    """Capture one complete bounded raw file with existing accounting diagnostics."""
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
        raise AccountingCaseError(code, reason, "/file") from None
    return raw


def validate_version(version: str) -> None:
    """Reject unregistered producer selection before acquisition or storage."""
    if type(version) is not str or version not in SUPPORTED_VERSIONS:
        raise AccountingCaseError("UNSUPPORTED_FORMAT", "UNSUPPORTED_PROMPTFOO_VERSION")


def _audit_promptfoo_bytes(
    raw: bytes, *, version: str, row_index: bool
) -> dict[str, object]:
    validate_version(version)
    try:
        observed, projection = packaged_ingestor(
            max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH
        ).buffered_projection(raw, "promptfoo-file", Source(SOURCE[0], version, None))
    except tuple(DIAGNOSTICS) as exc:
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
        raise AccountingCaseError(code, cast(str, diagnostic["reason"]), location) from None
    observation = observed.observation
    return _account_projection(
        projection,
        cast(dict[str, object], observation["raw"])["hash"],
        content_digest(observation),
        "/file",
        version=version,
        row_index=row_index,
    )


def audit_promptfoo_bytes(raw: bytes, *, version: str = SOURCE[1]) -> dict[str, object]:
    """Audit the supplied buffer, retaining the original projection and byte bounds."""
    return _audit_promptfoo_bytes(raw, version=version, row_index=False)


def audit_promptfoo_file(path: Path, *, version: str = SOURCE[1]) -> dict[str, object]:
    """Audit a pinned raw export once; expected-hash checks remain in audit_case."""
    validate_version(version)
    return audit_promptfoo_bytes(read_promptfoo_file(path), version=version)


def audit_promptfoo_row_index_file(
    path: Path, *, version: str = SOURCE[1]
) -> dict[str, object]:
    """Return a checked content-minimized row view after one bounded file read."""
    validate_version(version)
    try:
        report = _audit_promptfoo_bytes(read_promptfoo_file(path), version=version, row_index=True)
        packaged_validator("promptfoo_row_index.v1").validate(report)  # pyright: ignore[reportUnknownMemberType]
    except AccountingCaseError:
        raise
    except SchemaConfigurationError:
        raise AccountingCaseError(
            "SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT"
        ) from None
    except Exception:
        raise AccountingCaseError("INTERNAL_ERROR", "PROMPTFOO_ROW_INDEX_INVALID") from None
    return report
