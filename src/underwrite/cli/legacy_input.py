"""Bounded strict JSON admission for legacy CLI commands with arbitrary JSON roots."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

from underwrite_core.absence import AbsenceError, parse_date

from underwrite._json import DecodeError, decode_json
from underwrite.cli.local_json import read_source, validate
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

CONFIG_MAX_BYTES = 1024 * 1024
SCAN_MAX_BYTES = 4 * 1024 * 1024
JSON_MAX_DEPTH = 64
POLICY_MAX_ENTRIES = 1024


class AdmissionError(ValueError):
    """Finite refusal with a structural location, never caller content."""

    def __init__(
        self,
        code: str,
        reason: str,
        location: str,
        *,
        limit_kind: str | None = None,
        limit: int | None = None,
    ) -> None:
        self.code = code
        self.reason = reason
        self.location = location
        self.limit_kind = limit_kind
        self.limit = limit
        super().__init__(reason)

    def details(self) -> dict[str, object]:
        result: dict[str, object] = {"reason": self.reason, "location": self.location}
        if self.limit_kind is not None and self.limit is not None:
            result.update({"limit_kind": self.limit_kind, "limit": self.limit})
        return result


def read_bytes(path: Path, max_bytes: int, location: str) -> bytes:
    """Use the existing bounded regular-file reader without a canonical digest."""
    try:
        return read_source(path, max_bytes, location, "INVALID_JSON_INPUT", AdmissionError)
    except AdmissionError as exc:
        if exc.code == "INPUT_LIMIT_EXCEEDED":
            raise AdmissionError(
                "INPUT_LIMIT_EXCEEDED",
                "INPUT_LIMIT_EXCEEDED",
                location,
                limit_kind="bytes",
                limit=max_bytes,
            ) from None
        if exc.code in {"SOURCE_READ_FAILED", "UNSUPPORTED_PLATFORM"}:
            raise AdmissionError(exc.code, exc.code, location) from None
        raise


def decode_bytes(raw: bytes, location: str) -> object:
    """Decode the selected bytes; scalar, array and large finite integers remain valid."""
    try:
        return decode_json(raw, JSON_MAX_DEPTH)
    except DecodeError as exc:
        if exc.args == ("DEPTH_LIMIT_EXCEEDED",):
            raise AdmissionError(
                "INPUT_LIMIT_EXCEEDED",
                "INPUT_LIMIT_EXCEEDED",
                location,
                limit_kind="depth",
                limit=JSON_MAX_DEPTH,
            ) from None
        raise AdmissionError("INVALID_JSON_INPUT", "INVALID_JSON_INPUT", location) from None


def read_json(path: Path, max_bytes: int, location: str) -> object:
    return decode_bytes(read_bytes(path, max_bytes, location), location)


def validate_policy(value: object, schema: str, reason: str, location: str) -> None:
    """Let the installed policy contract own object and field shape."""
    try:
        validate(packaged_validator(schema), value, AdmissionError, reason, location)
    except AdmissionError:
        raise AdmissionError(reason, reason, location) from None
    except SchemaConfigurationError:
        raise AdmissionError(
            "SCHEMA_CONFIGURATION_ERROR", "SCHEMA_CONFIGURATION_ERROR", location
        ) from None


def require_date(value: str, location: str) -> date:
    """Translate the pure date refusal at one legacy CLI input boundary."""
    try:
        return parse_date(value)
    except AbsenceError:
        raise AdmissionError("INVALID_DATE", "INVALID_DATE", location) from None


def validate_date(value: str | None) -> date | None:
    """Validate a supplied date eagerly while leaving absent-clock semantics to the owner."""
    return None if value is None else require_date(value, "/now")


def limit_entries(count: int, location: str) -> None:
    if count > POLICY_MAX_ENTRIES:
        raise AdmissionError(
            "INPUT_LIMIT_EXCEEDED",
            "INPUT_LIMIT_EXCEEDED",
            location,
            limit_kind="entries",
            limit=POLICY_MAX_ENTRIES,
        )


def limit_policy_entries(value: object, key: str, location: str) -> None:
    """Count a present policy collection before schema validation materializes errors."""
    if not isinstance(value, dict):
        return
    document = cast(dict[str, object], value)
    entries = document.get(key)
    if isinstance(entries, dict):
        limit_entries(len(cast(dict[str, object], entries)), location)
    elif isinstance(entries, list):
        limit_entries(len(cast(list[object], entries)), location)
