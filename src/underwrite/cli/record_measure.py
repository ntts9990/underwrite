"""Card: measurement-record | Paper: measurement_record.v1 | Stage: C30.

In-process measurement and capture of the same two selected byte buffers.
"""

from __future__ import annotations

import argparse
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.cli import measurement_flow
from underwrite.cli.evidence import (
    EvidenceError,
    ReportOutput,
    run_cli,
)
from underwrite.cli.local_json import Validator, decode_document, read_source, validate
from underwrite.cli.record_capture import (
    STORAGE_FAILURE_EXIT,
    admit_record_response,
    render_record_response,
    store_captured_record,
)
from underwrite.cli.records import OUTPUT_MAX_BYTES, record_error_payload
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import (
    RECORD_READ_MAX_BYTES,
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
)

SCHEMA = "measurement_record.v1"
COMMAND = "records measure"


@dataclass(frozen=True)
class _Contracts:
    aggregate: Validator
    storage: Validator
    read: Validator
    measurement_error: Validator
    record_error: Validator
    verification: Validator


def _contracts() -> _Contracts:
    aggregate = packaged_validator("measurement_record.v1")
    schema = cast(dict[str, Any], aggregate.schema)
    storage = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/storage"})
    packaged_validator("observation.v1")
    read_validator, _ = measurement_flow.measurement_validators()
    return _Contracts(
        cast(Validator, aggregate),
        cast(Validator, storage),
        read_validator,
        cast(Validator, packaged_validator("measurement_error.v1")),
        cast(Validator, packaged_validator("record_error.v1")),
        cast(Validator, packaged_validator("record_verification.v1")),
    )


def _check(validator: Validator, value: object) -> None:
    validate(validator, value, EvidenceError, "INTERNAL_ERROR")


def _identifier(value: str) -> str:
    try:
        return record_id(value)
    except FormatError:
        raise argparse.ArgumentTypeError("valid new record ID required") from None


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--observation", type=Path, required=True, help="Entire native observation to retain."
    )
    parser.add_argument(
        "--policy", type=Path, required=True, help="Entire explicit measurement policy to retain."
    )
    parser.add_argument("--root", type=Path, required=True, help="Explicit private record root.")
    parser.add_argument("--id", type=_identifier, required=True, help="New immutable record ID.")
    parser.add_argument("--json", action="store_true", help="Emit measurement_record.v1 JSON.")
    parser.description = (
        "Measure native evidence in process and retain both entire selected files, the actual "
        "read or diagnostic, and invocation. Complete malformed files are retained privately; "
        "a diagnostic consumes the ID. Source pointers are not followed and no producer is run. "
        "Capture is not an atomic producer snapshot, provenance verification, or replay. "
        "Exit 0 means a read saved (including not_measured), 2 a diagnostic saved, and 3 storage "
        "failed. A saved read does not establish approval."
    )


def _analyze(
    paths: dict[measurement_flow.InputRole, Path],
) -> tuple[dict[measurement_flow.InputRole, bytes], dict[str, object], dict[str, Any]]:
    captured: dict[measurement_flow.InputRole, bytes] = {}
    sources: dict[str, object] = {}
    errors: dict[measurement_flow.InputRole, measurement_flow.MeasurementError] = {}
    for role, path in paths.items():
        maximum, _ = measurement_flow.input_limits(role)
        try:
            raw = read_source(
                path,
                maximum,
                "/" + role,
                "INVALID_" + role.upper(),
                measurement_flow.MeasurementError,
            )
        except SchemaConfigurationError:
            raise
        except Exception as exc:
            error = measurement_flow.measurement_error(exc)
            errors[role] = error
            sources[role] = {"state": "unavailable", "error": error.payload}
        else:
            captured[role] = raw
            sources[role] = {"state": "captured", "raw_hash": digest_bytes(raw), "bytes": len(raw)}

    phase = "analysis"

    def reader(role: measurement_flow.InputRole) -> tuple[dict[str, Any], str]:
        nonlocal phase
        if role in errors:
            phase = "acquisition"
            raise errors[role]
        maximum, depth = measurement_flow.input_limits(role)
        return decode_document(
            captured[role],
            maximum,
            depth,
            "/" + role,
            "INVALID_" + role.upper(),
            measurement_flow.MeasurementError,
        )

    try:
        analysis: dict[str, Any] = {
            "state": "result",
            "report": measurement_flow.read_measurement(reader),
        }
    except SchemaConfigurationError:
        raise
    except Exception as exc:
        analysis = {
            "state": "error",
            "phase": phase,
            "error": measurement_flow.measurement_error(exc).payload,
        }
    return captured, sources, analysis


def _files(
    paths: dict[measurement_flow.InputRole, Path],
    root: Path,
    identifier: str,
    captured: dict[measurement_flow.InputRole, bytes],
    envelope: dict[str, Any],
    contracts: _Contracts,
) -> tuple[CapturedRecordFile, ...]:
    analysis = envelope["analysis"]
    result = analysis["state"] == "result"
    document = analysis["report"] if result else analysis["error"]
    _check(contracts.read if result else contracts.measurement_error, document)
    encoded = canonical_bytes(document) + b"\n"
    if len(encoded) > min(RECORD_READ_MAX_BYTES, measurement_flow.OUTPUT_MAX_BYTES):
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    invocation = {
        "command": COMMAND,
        "execution_mode": "in_process",
        "arguments": {
            "observation_path_display": unicodedata.normalize("NFC", str(paths["observation"])),
            "policy_path_display": unicodedata.normalize("NFC", str(paths["policy"])),
            "root_display": unicodedata.normalize("NFC", str(root)),
            "record_id": identifier,
        },
        "sources": envelope["sources"],
        "analysis_state": analysis["state"],
        "analysis_exit_code": 0 if result else 2,
        "analysis_exit_code_scope": "component_contract_not_os_process",
        "aggregate_outcome_location": "command_output",
    }
    files = [
        CapturedRecordFile(
            "analysis/read.json" if result else "analysis/error.json",
            "result" if result else "log",
            encoded,
            "application/json",
        ),
        CapturedRecordFile(
            "invocation.json", "log", canonical_bytes(invocation) + b"\n", "application/json"
        ),
    ]
    files[:0] = [
        CapturedRecordFile(f"source/{role}.bin", "source", raw) for role, raw in captured.items()
    ]
    return tuple(files)


def _analysis_text(analysis: dict[str, Any]) -> str:
    if analysis["state"] == "error":
        error = analysis["error"]
        return f"Analysis failed during {analysis['phase']}: {error['code']} ({error['reason']})."
    return (
        "Measurement read produced for interpretation; source claims remain unverified. "
        "Exit 0 does not establish approval."
    )


def render_human(report: dict[str, object]) -> str:
    return render_record_response(
        report, analysis_text=_analysis_text(cast(dict[str, Any], report["analysis"]))
    )


def run_record_measure(
    observation: Path, policy: Path, root: Path, identifier: str, *, as_json: bool = True
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    contracts = _contracts()
    paths: dict[measurement_flow.InputRole, Path] = {"observation": observation, "policy": policy}
    captured, sources, analysis = _analyze(paths)
    envelope: dict[str, Any] = {
        "schema": SCHEMA,
        "command": COMMAND,
        "sources": sources,
        "analysis": analysis,
        "exit_code": 0 if analysis["state"] == "result" else 2,
    }
    try:
        files = _files(paths, root, identifier, captured, envelope, contracts)
    except CanonicalizationError:
        envelope["storage"] = {
            "state": "error",
            "error": record_error_payload(RecordError("INVALID_SPEC", "/spec/files")),
            "published_record": None,
        }
        envelope["exit_code"] = STORAGE_FAILURE_EXIT
        admit_record_response(
            envelope,
            aggregate_validator=contracts.aggregate,
            storage_validator=contracts.storage,
            identifier=identifier,
            count=0,
            analysis_text=_analysis_text(analysis),
            as_json=as_json,
            max_bytes=OUTPUT_MAX_BYTES,
        )
        return envelope
    admit_record_response(
        envelope,
        aggregate_validator=contracts.aggregate,
        storage_validator=contracts.storage,
        identifier=identifier,
        count=len(files),
        analysis_text=_analysis_text(analysis),
        as_json=as_json,
        max_bytes=OUTPUT_MAX_BYTES,
    )
    spec = CapturedRecordSpec(
        record_id=identifier,
        kind="measurement.event",
        label="Native measurement capture",
        metadata={"analysis_state": analysis["state"]},
        links=(),
        selection_excluded=None,
        rerun_verification={"status": "not_performed", "reference": None},
        provenance={"verified": False, "attestation_ref": None},
        files=files,
    )
    envelope["storage"] = store_captured_record(
        root,
        spec,
        verification_validator=contracts.verification,
        record_error_validator=contracts.record_error,
    )
    if envelope["storage"]["state"] == "error":
        envelope["exit_code"] = STORAGE_FAILURE_EXIT
    return envelope


def produce(args: argparse.Namespace) -> dict[str, object]:
    return run_record_measure(
        cast(Path, args.observation),
        cast(Path, args.policy),
        cast(Path, args.root),
        cast(str, args.id),
        as_json=cast(bool, args.json),
    )


def main(argv: list[str]) -> int:
    return run_cli(
        COMMAND,
        argv,
        configure,
        produce,
        ReportOutput(
            SCHEMA,
            max_bytes=OUTPUT_MAX_BYTES,
            human=render_human,
            exit_status=lambda report: cast(int, report["exit_code"]),
        ),
    )
