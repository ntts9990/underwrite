"""Card: measurement-replay | Paper: measurement_replay.v1 | Stage: C31.

Compare current in-process calculation with a retained outcome from verified bytes.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import canonical_bytes, content_digest

from underwrite.cli import measure
from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.cli.local_json import Validator, decode_document, validate
from underwrite.cli.records import OUTPUT_MAX_BYTES, record_error_payload
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.measurement_contract.read import validate_event_read
from underwrite.measurement_contract.semantics import ContractError
from underwrite.service.records import (
    RECORD_READ_MAX_BYTES,
    RecordError,
    RecordFileSnapshot,
    read_record,
    read_record_file,
)

SCHEMA = "measurement_replay.v1"
COMMAND = "records replay-measure"
ELIGIBLE_DIAGNOSTIC_CODES = frozenset(
    {"INVALID_OBSERVATION", "INVALID_POLICY", "UNSUPPORTED_PROFILE", "INPUT_LIMIT_EXCEEDED"}
)
VERIFICATION_SCOPE = "selected_files_against_one_manifest"
COMPARISON_SCOPE = "canonical_outcome_kind_and_full_document"
EXECUTION_MODE = "current_implementation_in_process"
LIMITATIONS = (
    "Current calculation agreement does not establish historical environment identity.",
    "Selected-file integrity does not authenticate producers or source claims.",
    "Agreement does not establish representative sampling or inference assumptions.",
    "A matching diagnostic remains an error; matching a not_measured read does not grant "
    "directional inference or approval.",
)


class _RecordFailure(EvidenceError):
    """Bridge only the record owner's validated finite diagnostic into the runner."""

    def __init__(self, error: RecordError, validator: Validator) -> None:
        super().__init__("INTERNAL_ERROR", "INTERNAL_ERROR")
        payload = record_error_payload(error)
        validate(validator, payload, EvidenceError, "INTERNAL_ERROR")
        self.payload = payload


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, required=True, help="Explicit private record root.")
    parser.add_argument("--id", required=True, help="Retained measurement record ID.")
    parser.add_argument("--json", action="store_true", help="Emit measurement_replay.v1 JSON.")
    parser.description = (
        "Recalculate from fixed retained observation/policy bytes without invoking a producer. "
        "Compare the entire current outcome with the saved read or deterministic diagnostic. "
        "Exit 0 means agreement, 1 disagreement, and 2 refusal; agreement is not approval "
        "or proof of the historical environment. Only selected files are verified."
    )


def _invalid(reason: str) -> EvidenceError:
    return EvidenceError("INVALID_INPUT", reason, "/record")


def _identity(selected: RecordFileSnapshot) -> dict[str, object]:
    return {"name": selected.name, "sha256": selected.sha256, "bytes": len(selected.data)}


def _stored_outcome(
    selected: RecordFileSnapshot, read_validator: Validator, error_validator: Validator
) -> dict[str, Any]:
    try:
        document, _ = decode_document(
            selected.data, RECORD_READ_MAX_BYTES, 64, "/record", "INVALID_INPUT", EvidenceError
        )
        result = selected.name == "analysis/read.json"
        validate(
            read_validator if result else error_validator,
            document,
            EvidenceError,
            "INVALID_INPUT",
            "/record",
        )
        if result:
            validate_event_read(document)
    except (EvidenceError, ContractError):
        raise _invalid("INVALID_STORED_MEASUREMENT_OUTCOME") from None
    if result:
        return {"state": "result", "report": document}
    if document["code"] not in ELIGIBLE_DIAGNOSTIC_CODES:
        raise _invalid("UNSUPPORTED_STORED_MEASUREMENT_DIAGNOSTIC")
    return {"state": "error", "error": document}


def _calculate(sources: dict[measure.InputRole, RecordFileSnapshot]) -> dict[str, Any]:
    def reader(role: measure.InputRole) -> tuple[dict[str, Any], str]:
        maximum, depth = measure.input_limits(role)
        return decode_document(
            sources[role].data,
            maximum,
            depth,
            "/" + role,
            "INVALID_" + role.upper(),
            measure.MeasurementError,
        )

    try:
        return {"state": "result", "report": measure.read_measurement(reader)}
    except SchemaConfigurationError:
        raise
    except Exception as exc:
        error = measure.measurement_error(exc)
        if error.payload["code"] == "SCHEMA_CONFIGURATION_ERROR":
            raise SchemaConfigurationError("INVALID_MEASUREMENT_SCHEMA") from None
        if error.payload["code"] not in ELIGIBLE_DIAGNOSTIC_CODES:
            raise EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR") from None
        return {"state": "error", "error": error.payload}


def run_replay_measure(root: Path, identifier: str) -> dict[str, object]:
    """Read three fixed files against one manifest, then compare complete outcomes."""
    aggregate = packaged_validator(SCHEMA)
    record_error = packaged_validator("record_error.v1")
    packaged_validator("evidence_error.v1")
    packaged_validator("observation.v1")
    read_validator, _ = measure.measurement_validators()
    error_validator = packaged_validator("measurement_error.v1")
    try:
        snapshot = read_record(root, identifier)
        if snapshot.manifest["kind"] != "measurement.event":
            raise EvidenceError("UNSUPPORTED_PROFILE", "UNSUPPORTED_MEASUREMENT_RECORD", "/record")
        files = {
            entry["name"]: entry for entry in cast(list[dict[str, Any]], snapshot.manifest["files"])
        }
        outcomes = [name for name in ("analysis/read.json", "analysis/error.json") if name in files]
        if len(outcomes) > 1:
            raise _invalid("AMBIGUOUS_MEASUREMENT_OUTCOME")
        if not outcomes or any(
            f"source/{role}.bin" not in files for role in ("observation", "policy")
        ):
            raise _invalid("INCOMPLETE_MEASUREMENT_RECORD")
        outcome_name = outcomes[0]
        expected_roles = {
            "source/observation.bin": "source",
            "source/policy.bin": "source",
            outcome_name: "result" if outcome_name == "analysis/read.json" else "log",
        }
        if any(files[name]["role"] != role for name, role in expected_roles.items()):
            raise EvidenceError("UNSUPPORTED_PROFILE", "UNSUPPORTED_MEASUREMENT_RECORD", "/record")

        def selected(name: str, maximum: int) -> RecordFileSnapshot:
            value = read_record_file(root, identifier, name, max_bytes=maximum)
            if value.manifest_digest != snapshot.digest:
                raise EvidenceError(
                    "INPUT_BINDING_MISMATCH", "RECORD_MANIFEST_IDENTITY_MISMATCH", "/record"
                )
            return value

        sources: dict[measure.InputRole, RecordFileSnapshot] = {
            role: selected(f"source/{role}.bin", measure.input_limits(role)[0])
            for role in cast(tuple[measure.InputRole, ...], ("observation", "policy"))
        }
        saved = selected(outcome_name, RECORD_READ_MAX_BYTES)
    except RecordError as exc:
        raise _RecordFailure(exc, record_error) from None
    stored = _stored_outcome(saved, read_validator, error_validator)
    current = _calculate(sources)
    matches = canonical_bytes(stored) == canonical_bytes(current)
    report: dict[str, object] = {
        "schema": SCHEMA,
        "command": COMMAND,
        "record_id": identifier,
        "manifest_digest": snapshot.digest,
        "verification_scope": VERIFICATION_SCOPE,
        "sources": {role: _identity(value) for role, value in sources.items()},
        "stored": {
            **_identity(saved),
            "canonical_digest": content_digest(stored),
            "outcome": stored,
        },
        "recomputed": {"canonical_digest": content_digest(current), "outcome": current},
        "comparison": {"state": "match" if matches else "mismatch", "scope": COMPARISON_SCOPE},
        "execution_mode": EXECUTION_MODE,
        "limitations": list(LIMITATIONS),
        "exit_code": 0 if matches else 1,
    }
    validate(aggregate, report, EvidenceError, "INTERNAL_ERROR")
    if len(canonical_bytes(report)) + 1 > OUTPUT_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    return report


def main(argv: list[str]) -> int:
    return run_cli(
        COMMAND,
        argv,
        configure,
        lambda args: run_replay_measure(cast(Path, args.root), cast(str, args.id)),
        ReportOutput(
            SCHEMA,
            max_bytes=OUTPUT_MAX_BYTES,
            exit_status=lambda report: cast(int, report["exit_code"]),
        ),
    )
