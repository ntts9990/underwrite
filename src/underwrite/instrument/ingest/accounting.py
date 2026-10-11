"""Promptfoo accounting adapter over the existing safe, single-buffer case reader."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from underwrite.instrument.evidence.accounting import AccountingError, audit_promptfoo
from underwrite.instrument.ingest.application import DIAGNOSTICS
from underwrite.instrument.ingest.inspection import (
    InspectionError,
    inspect_artifact_with_projection,
)

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
    """Audit only what one pinned artifact declares, retaining its original identity."""
    try:
        report, projection = inspect_artifact_with_projection(path, required_source=SOURCE)
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
    summary = projection.payload.get("results")
    if not isinstance(summary, dict):
        raise AccountingCaseError("INTERNAL_ERROR", "INTERNAL_ERROR")
    source = cast(dict[str, object], summary)
    try:
        counted = audit_promptfoo(source.get("stats"), source.get("results"))
    except AccountingError as exc:
        if str(exc) == "ROW_LIMIT_EXCEEDED":
            raise AccountingCaseError(
                "COMPUTATION_LIMIT_EXCEEDED", "ROW_LIMIT_EXCEEDED", "/artifact"
            ) from exc
        raise AccountingCaseError("INVALID_INPUT", str(exc), "/artifact") from exc
    observation = report["observation"]
    if not isinstance(observation, dict):
        raise AccountingCaseError("INTERNAL_ERROR", "INTERNAL_ERROR")
    observation_digest = cast(dict[str, object], observation).get("digest")
    if not isinstance(observation_digest, str):
        raise AccountingCaseError("INTERNAL_ERROR", "INTERNAL_ERROR")
    return {
        "schema": "promptfoo_accounting.v1",
        "source": {"format": SOURCE[0], "format_version": SOURCE[1]},
        "raw_hash": report["raw_hash"],
        "observation_digest": observation_digest,
        **counted,
        "limitations": list(LIMITATIONS),
    }
