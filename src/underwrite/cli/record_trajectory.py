"""Card: trajectory-record | Paper: trajectory_record.v2 | Stage: storage-capture.

Retain one captured exchange and reopen its analysis without executing its producer.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.audit_trajectory import trajectory_cli_error
from underwrite.cli.evidence import EvidenceError, ReportOutput, output_line_size, run_cli
from underwrite.cli.local_json import Validator, decode_document, validate
from underwrite.cli.record_capture import (
    STORAGE_FAILURE_EXIT,
    admit_record_response,
    render_record_response,
    saved_record,
    storage_validator,
    store_captured_record,
)
from underwrite.instrument.evidence.goppi_trajectory import (
    MAX_REPORT_BYTES,
    MEMBERS,
    RESULT_SCHEMA,
    RESULT_SCHEMA_V2,
    TrajectoryError,
)
from underwrite.instrument.ingest.goppi_trajectory import (
    HISTORY_SCHEMA,
    HISTORY_SCHEMA_V2,
    TrajectoryInputError,
    audit_captured_goppi_trajectory,
    capture_goppi_trajectory,
    trajectory_diagnostic_history_matches,
    trajectory_member_limits,
    validate_trajectory_verifier_selection,
)
from underwrite.instrument.ingest.goppi_verifier import TrajectoryVerifier, VerifierError
from underwrite.instrument.ingest.local_capture import CapturedBytes, CaptureError
from underwrite.instrument.ingest.schema import packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import (
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
    RecordSnapshot,
    read_record,
    read_record_file,
    verify_record,
)

SCHEMA = "trajectory_record.v1"
READ_SCHEMA = "trajectory_record_read.v1"
SCHEMA_V2 = "trajectory_record.v2"
READ_SCHEMA_V2 = "trajectory_record_read.v2"
COMMAND = "records audit-trajectory"
READ_COMMAND = "records read-trajectory"
KIND = "goppi.trajectory-review"
LAYOUT = "trajectory-record.v1"
LAYOUT_V2 = "trajectory-record.v2"
WRAPPER_MAX_BYTES = 64 * 1024
OUTPUT_MAX_BYTES = MAX_REPORT_BYTES + WRAPPER_MAX_BYTES
HISTORY_NAME = "verification/history.json"
INVOCATION_NAME = "invocation.json"
RESULT_NAME = "analysis/trajectory.json"
ERROR_NAME = "analysis/error.json"
FILE_COUNT = len(MEMBERS) + 3


@dataclass(frozen=True)
class _Contracts:
    aggregate: Validator
    storage: Validator
    analysis: Validator
    history: Validator
    evidence_error: Validator
    record_error: Validator
    verification: Validator
    reopen: Validator


def _contracts(layout: str) -> _Contracts:
    if layout == LAYOUT:
        schema, analysis, history = SCHEMA, RESULT_SCHEMA, HISTORY_SCHEMA
    else:
        schema, analysis, history = SCHEMA_V2, RESULT_SCHEMA_V2, HISTORY_SCHEMA_V2
    aggregate = packaged_validator(schema)
    return _Contracts(
        cast(Validator, aggregate),
        storage_validator(aggregate),
        cast(Validator, packaged_validator(analysis)),
        cast(Validator, packaged_validator(history)),
        cast(Validator, packaged_validator("evidence_error.v1")),
        cast(Validator, packaged_validator("record_error.v1")),
        cast(Validator, packaged_validator("record_verification.v1")),
        cast(Validator, packaged_validator(READ_SCHEMA_V2)),
    )


def _identifier(value: str) -> str:
    try:
        return record_id(value)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None


def _metadata(layout: str, state: str) -> dict[str, str]:
    return {"layout": layout, "analysis_state": state}


def _invocation(layout: str, state: str, history: Mapping[str, object]) -> dict[str, object]:
    invocation: dict[str, object] = {
        "command": COMMAND,
        "layout": layout,
        "analysis_state": state,
    }
    if layout == LAYOUT:
        invocation.update(
            native_operation="run verify-paired",
            execution_scope="offline_content_closure",
        )
    else:
        invocation.update(
            verifier_report_state=history["verifier_report_state"],
            configured_verifier_source=history["verifier_source"],
            verification_scope="report_only",
        )
    return invocation


def _layout(state: str) -> dict[str, tuple[str, str, int]]:
    limits = trajectory_member_limits()
    return {
        **{
            f"source/{name}": ("source", "application/octet-stream", limits[name])
            for name in MEMBERS
        },
        RESULT_NAME if state == "result" else ERROR_NAME: (
            "result" if state == "result" else "log",
            "application/json",
            MAX_REPORT_BYTES,
        ),
        INVOCATION_NAME: ("log", "application/json", WRAPPER_MAX_BYTES),
        HISTORY_NAME: ("verification", "application/json", WRAPPER_MAX_BYTES),
    }


def _bounded_json(document: object, maximum: int) -> bytes:
    raw = canonical_bytes(document) + b"\n"
    if len(raw) > maximum:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    return raw


def _files(
    captured: Mapping[str, CapturedBytes], analysis: dict[str, Any], history: dict[str, object]
) -> tuple[CapturedRecordFile, ...]:
    state = analysis["state"]
    documents = {
        INVOCATION_NAME: _invocation(LAYOUT_V2, state, history),
        HISTORY_NAME: history,
        RESULT_NAME if state == "result" else ERROR_NAME: analysis["report"]
        if state == "result"
        else analysis["error"],
    }
    return tuple(
        CapturedRecordFile(
            name,
            role,
            captured[name.removeprefix("source/")].data
            if name.startswith("source/")
            else _bounded_json(documents[name], limit),
            media,
        )
        for name, (role, media, limit) in _layout(state).items()
    )


def _analysis_text(analysis: dict[str, Any]) -> str:
    if analysis["state"] == "error":
        return f"Analysis diagnostic: {analysis['error']['code']} ({analysis['error']['reason']})."
    return "Trajectory comparison retained; differences do not establish causality or approval."


def render_human(report: dict[str, object]) -> str:
    return render_record_response(
        report, analysis_text=_analysis_text(cast(dict[str, Any], report["analysis"]))
    )


def run_record_trajectory(
    exchange: Path,
    root: Path,
    identifier: str,
    *,
    verifier: TrajectoryVerifier | None = None,
    goppi_executable: Path | None = None,
    as_json: bool = True,
) -> dict[str, object]:
    """Capture and retain a report-only analysis; verifier failures never consume an ID."""
    identifier = _identifier(identifier)
    try:
        validate_trajectory_verifier_selection(verifier, goppi_executable)
    except TrajectoryInputError as exc:
        raise trajectory_cli_error(exc) from None
    contracts = _contracts(LAYOUT_V2)
    try:
        captured = capture_goppi_trajectory(exchange)
        outcome = audit_captured_goppi_trajectory(
            captured, verifier=verifier, goppi_executable=goppi_executable
        )
    except (CaptureError, TrajectoryInputError, TrajectoryError, VerifierError) as exc:
        raise trajectory_cli_error(exc) from None
    if outcome.error is not None:
        error = outcome.error
        analysis: dict[str, Any] = {
            "state": "error",
            "error": EvidenceError(error.code, error.reason, error.location).payload,
        }
    else:
        analysis = {"state": "result", "report": outcome.report}
    validate(
        contracts.analysis if outcome.error is None else contracts.evidence_error,
        analysis.get("report", analysis.get("error")),
        EvidenceError,
        "INTERNAL_ERROR",
    )
    history = outcome.verification_history
    validate(contracts.history, history, EvidenceError, "INTERNAL_ERROR")
    files = _files(captured, analysis, history)
    envelope: dict[str, Any] = {
        "schema": SCHEMA_V2,
        "command": COMMAND,
        "source": {"state": "captured", "source_members": history["source_members"]},
        "analysis": analysis,
        "verifier_report_history": history,
        "exit_code": 0 if outcome.error is None else 2,
    }
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
        identifier,
        KIND,
        "Goppi trajectory review",
        _metadata(LAYOUT_V2, analysis["state"]),
        (),
        None,
        {"status": "not_performed", "reference": None},
        {"verified": False, "attestation_ref": None},
        files,
    )
    envelope["storage"] = store_captured_record(
        root, spec, contracts.verification, contracts.record_error
    )
    if envelope["storage"]["state"] == "error":
        envelope["exit_code"] = STORAGE_FAILURE_EXIT
    return envelope


def _require(condition: bool) -> None:
    if not condition:
        raise EvidenceError(
            "INPUT_BINDING_MISMATCH", "TRAJECTORY_RECORD_BINDING_MISMATCH", "/record"
        )


def _manifest(
    snapshot: RecordSnapshot, identifier: str, layout: str
) -> tuple[str, list[dict[str, Any]]]:
    manifest = cast(dict[str, Any], snapshot.manifest)
    state = manifest["metadata"].get("analysis_state")
    _require(type(state) is str and state in ("result", "error"))
    _require(manifest["record_id"] == identifier and manifest["kind"] == KIND)
    _require(manifest["metadata"] == _metadata(layout, state))
    _require(manifest["selection"] == {"scope": "explicit_files", "excluded": None})
    _require(
        manifest["links"] == []
        and manifest["rerun_verification"] == {"status": "not_performed", "reference": None}
    )
    _require(
        manifest["provenance"]["verified"] is False
        and manifest["provenance"]["attestation_ref"] is None
    )
    expected = _layout(state)
    files = cast(list[dict[str, Any]], manifest["files"])
    _require(len(files) == len(expected) and {entry["name"] for entry in files} == set(expected))
    by_name = {entry["name"]: entry for entry in files}
    for name, (role, media, maximum) in expected.items():
        entry = by_name[name]
        _require(
            entry["role"] == role
            and entry["media_type"] == media
            and entry["source_locator"] is None
        )
        _require(type(entry["bytes"]) is int and 0 <= entry["bytes"] <= maximum)
    sources = [
        {
            "name": name,
            "raw_hash": by_name[f"source/{name}"]["sha256"],
            "bytes": by_name[f"source/{name}"]["bytes"],
        }
        for name in MEMBERS
    ]
    return state, sources


def _read_bound(
    root: Path, identifier: str, name: str, maximum: int, digest: str
) -> dict[str, Any]:
    selected = read_record_file(root, identifier, name, max_bytes=maximum)
    _require(selected.manifest_digest == digest and selected.record_id == identifier)
    document, _ = decode_document(
        selected.data, maximum, 64, "/record", "INVALID_INPUT", EvidenceError
    )
    return document


def _record_layout(snapshot: RecordSnapshot) -> str:
    manifest = cast(dict[str, Any], snapshot.manifest)
    metadata = manifest.get("metadata")
    _require(type(metadata) is dict)
    layout = cast(dict[str, Any], metadata).get("layout")
    _require(layout in (LAYOUT, LAYOUT_V2))
    return cast(str, layout)


def _bindings(
    layout: str,
    analysis: dict[str, Any],
    history: dict[str, Any],
    sources: list[dict[str, Any]],
) -> None:
    actual = history["source_members"]
    _require(all(type(item["bytes"]) is int for item in actual))
    _require(actual == sources)
    if analysis["state"] == "error":
        error = analysis["error"]
        history_state = history["state"] if layout == LAYOUT else history["verifier_report_state"]
        _require(
            trajectory_diagnostic_history_matches(error["code"], error["reason"], history_state)
        )
    else:
        report = analysis["report"]
        identities = report["input_members"]
        _require(all(type(item["bytes"]) is int for item in identities))
        expected = [
            {"path": item["name"], "sha256": item["raw_hash"], "bytes": item["bytes"]}
            for item in sources
        ]
        _require(identities == expected)
        if layout == LAYOUT:
            _require(history["state"] == "verified")
            _require(
                report["trust_scope"]["native_verification"] == "local_goppi_same_byte_snapshot"
            )
        else:
            _require(history["verifier_report_state"] == "report_accepted")
            trust = report["trust_scope"]
            for field in (
                "verifier_report_state",
                "verifier_source",
                "verifier_identity_verified",
                "response_snapshot_binding",
                "snapshot_postcheck",
                "content_closure",
                "authenticity",
                "source_authentication_independently_verified",
                "producer_projection_independently_verified",
            ):
                _require(trust[field] == history[field])


def _interpretation(layout: str, history: Mapping[str, Any]) -> dict[str, object]:
    if layout == LAYOUT:
        return {
            "state": "legacy_not_performed"
            if history["state"] == "not_performed"
            else "legacy_unattributed_report",
            "verifier_identity_verified": False,
            "current_native_verification": "not_performed",
            "limitations": ["verifier_identity_not_recorded"],
        }
    return {
        "state": "current_not_performed"
        if history["verifier_report_state"] == "not_performed"
        else "current_report_only",
        "verifier_identity_verified": False,
        "current_native_verification": "not_performed",
        "limitations": [],
    }


def run_read_trajectory(root: Path, identifier: str) -> dict[str, object]:
    """Read retained analysis under its exact layout; verify current stored bytes only."""
    identifier = _identifier(identifier)
    try:
        snapshot = read_record(root, identifier)
        layout = _record_layout(snapshot)
        contracts = _contracts(layout)
        state, sources = _manifest(snapshot, identifier, layout)
        history = _read_bound(root, identifier, HISTORY_NAME, WRAPPER_MAX_BYTES, snapshot.digest)
        validate(contracts.history, history, EvidenceError, "INVALID_INPUT", "/record")
        invocation = _read_bound(
            root, identifier, INVOCATION_NAME, WRAPPER_MAX_BYTES, snapshot.digest
        )
        _require(invocation == _invocation(layout, state, history))
        document = _read_bound(
            root,
            identifier,
            RESULT_NAME if state == "result" else ERROR_NAME,
            MAX_REPORT_BYTES,
            snapshot.digest,
        )
        validate(
            contracts.analysis if state == "result" else contracts.evidence_error,
            document,
            EvidenceError,
            "INVALID_INPUT",
            "/record",
        )
        analysis = {"state": state, "report" if state == "result" else "error": document}
        _bindings(layout, analysis, history, sources)
        verification = verify_record(root, identifier)
        validate(contracts.verification, verification, EvidenceError, "INVALID_INPUT", "/record")
        _require(
            canonical_bytes(verification)
            == canonical_bytes(
                saved_record(identifier, snapshot.digest, FILE_COUNT)["verification"]
            )
        )
    except RecordError as exc:
        raise EvidenceError("INVALID_INPUT", "TRAJECTORY_RECORD_" + exc.code, "/record") from None
    envelope: dict[str, object] = {
        "schema": READ_SCHEMA_V2,
        "command": READ_COMMAND,
        "record_id": identifier,
        "manifest_digest": snapshot.digest,
        "source": {"state": "captured", "source_members": sources},
        "current_storage_verification": verification,
        "native_verification_performed_now": False,
        "record_layout": layout,
        "verification_interpretation": _interpretation(layout, history),
    }
    if layout == LAYOUT:
        envelope["historical_analysis"] = analysis
        envelope["historical_native_verification_history"] = history
    else:
        envelope["analysis"] = analysis
        envelope["verifier_report_history"] = history
    validate(contracts.reopen, envelope, EvidenceError, "INTERNAL_ERROR")
    if output_line_size(canonical_bytes(envelope)) > OUTPUT_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    return envelope


def configure_read(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, required=True, help="Private record root.")
    parser.add_argument("--id", type=_identifier, required=True, help="Immutable record ID.")
    parser.add_argument("--json", action="store_true", help="Emit typed trajectory record JSON.")
    parser.description = (
        "Reopen retained trajectory analysis and verify all stored bytes without executing Goppi. "
        "Historical native verification is not a fresh execution."
    )


def configure(parser: argparse.ArgumentParser) -> None:
    configure_read(parser)
    parser.add_argument("--exchange", type=Path, required=True)
    parser.add_argument(
        "--goppi-executable", type=Path, help="Operator-selected Goppi; defaults to goppi on PATH."
    )
    parser.description = (
        "Audit and retain the same complete Goppi exchange bytes, result or safe diagnostic, "
        "and configured-verifier report history. Exit 0: result saved; 2: diagnostic saved; "
        "3: storage failed. A saved diagnostic consumes the ID."
    )


def main(argv: list[str]) -> int:
    return run_cli(
        COMMAND,
        argv,
        configure,
        lambda args: run_record_trajectory(
            args.exchange,
            args.root,
            args.id,
            goppi_executable=args.goppi_executable,
            as_json=args.json,
        ),
        ReportOutput(
            SCHEMA_V2,
            OUTPUT_MAX_BYTES,
            lambda result: cast(int, result["exit_code"]),
            render_human,
        ),
    )


def main_read(argv: list[str]) -> int:
    return run_cli(
        READ_COMMAND,
        argv,
        configure_read,
        lambda args: run_read_trajectory(args.root, args.id),
        ReportOutput(
            READ_SCHEMA_V2,
            OUTPUT_MAX_BYTES,
            human=lambda result: (
                "Retained trajectory analysis reopened; stored bytes verified. "
                "Native verification was not performed now.\n" + canonical_bytes(result).decode()
            ),
        ),
    )
