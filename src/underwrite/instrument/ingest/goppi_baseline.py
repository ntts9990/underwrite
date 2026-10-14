"""Card: baseline-event | Paper: ADR-020 | Stage: producer-bridge.

Bounded read-once acquisition of the exact baseline exchange profile."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from jsonschema.exceptions import ValidationError

from underwrite.instrument.evidence.goppi_baseline import (
    FILES,
    MAX_CASES,
    BaselineDocuments,
    BaselineProjectionError,
    project_baseline_result,
)
from underwrite.instrument.ingest.local_capture import (
    CapturedBytes,
    CaptureError,
    CapturePolicy,
    RootReader,
    decode_captured_object,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import DecodeError, decode_json

DOCUMENT_MAX_BYTES = 1024 * 1024
ROWS_MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 32
CAPTURE_POLICY = CapturePolicy(len(FILES), ROWS_MAX_BYTES, 8 * 1024 * 1024, 1)


def _rows(captured: CapturedBytes) -> tuple[dict[str, Any], ...]:
    lines = captured.data.split(b"\n", MAX_CASES + 1)
    if lines[-1] == b"":
        lines.pop()
    location = "/exchange/rows.jsonl"
    if len(lines) > MAX_CASES:
        raise BaselineProjectionError(
            "INPUT_LIMIT_EXCEEDED", "BASELINE_ROW_LIMIT_EXCEEDED", location
        )
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        row_location = f"{location}/{index}"
        try:
            value = decode_json(line, MAX_DEPTH)
        except DecodeError:
            raise CaptureError("INVALID_INPUT", "CAPTURE_INVALID_JSON", row_location) from None
        if type(value) is not dict:
            raise CaptureError("INVALID_INPUT", "CAPTURE_JSON_OBJECT_REQUIRED", row_location)
        rows.append(cast(dict[str, Any], value))
    return tuple(rows)


def capture_baseline_files(exchange: Path) -> dict[str, CapturedBytes]:
    """Capture all five fixed regular files once before any document decoding."""
    with RootReader.open(exchange, CAPTURE_POLICY) as reader:
        return {
            name: reader.capture_relative(
                name,
                logical_name=name,
                max_bytes=ROWS_MAX_BYTES if name == "rows.jsonl" else DOCUMENT_MAX_BYTES,
                location="/exchange/" + name,
            )
            for name in FILES
        }


def decode_baseline_documents(captured: Mapping[str, CapturedBytes]) -> BaselineDocuments:
    """Decode the captured buffers; producer schema stays inert hashed bytes."""
    documents = {
        name: decode_captured_object(
            captured[name], max_depth=MAX_DEPTH, location="/exchange/" + name
        )
        for name in ("manifest.json", "export.json", "scope.json")
    }
    return BaselineDocuments(
        manifest=documents["manifest.json"],
        export=documents["export.json"],
        scope=documents["scope.json"],
        rows=_rows(captured["rows.jsonl"]),
        identities={name: (item.sha256, item.size) for name, item in captured.items()},
    )


def capture_baseline_documents(exchange: Path) -> BaselineDocuments:
    """Read only five fixed regular files and decode their retained bytes."""
    return decode_baseline_documents(capture_baseline_files(exchange))


def audit_baseline_exchange(exchange: Path) -> dict[str, object]:
    """Capture a completed exchange once and return its validated descriptive result."""
    result = project_baseline_result(capture_baseline_documents(exchange))
    try:
        validator = packaged_validator("evidence_result.v1")
        validator.validate(result)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError:
        raise BaselineProjectionError(
            "INTERNAL_ERROR", "BASELINE_RESULT_INVALID", "/projection"
        ) from None
    except SchemaConfigurationError:
        raise BaselineProjectionError(
            "SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT", "/projection"
        ) from None
    except Exception:
        raise BaselineProjectionError(
            "SCHEMA_CONFIGURATION_ERROR", "SCHEMA_EVALUATION_FAILED", "/projection"
        ) from None
    return result
