"""Capture one complete baseline exchange and its descriptive result in one record."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.cli.local_json import Validator, validate
from underwrite.cli.record_capture import (
    STORAGE_FAILURE_EXIT,
    admit_record_response,
    render_record_response,
    storage_validator,
    store_captured_record,
)
from underwrite.cli.records import OUTPUT_MAX_BYTES
from underwrite.instrument.evidence.goppi_baseline import (
    FILES,
    RESULT_SCHEMA,
    BaselineProjectionError,
    project_baseline_result,
)
from underwrite.instrument.ingest.goppi_baseline import (
    capture_baseline_files,
    decode_baseline_documents,
)
from underwrite.instrument.ingest.local_capture import CapturedBytes, CaptureError
from underwrite.instrument.ingest.schema import packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import (
    RECORD_READ_MAX_BYTES,
    CapturedRecordFile,
    CapturedRecordSpec,
)

SCHEMA = "baseline_record.v2"
COMMAND = "records audit-baseline"
PROFILE = "goppi-baseline-exchange-v2"
INVOCATION_MAX_BYTES = 2 * 1024


@dataclass(frozen=True)
class _Contracts:
    aggregate: Validator
    storage: Validator
    result: Validator
    evidence_error: Validator
    record_error: Validator
    verification: Validator


def _contracts() -> _Contracts:
    aggregate = packaged_validator(SCHEMA)
    storage = storage_validator(aggregate)
    return _Contracts(
        cast(Validator, aggregate),
        storage,
        cast(Validator, packaged_validator(RESULT_SCHEMA)),
        cast(Validator, packaged_validator("evidence_error.v1")),
        cast(Validator, packaged_validator("record_error.v1")),
        cast(Validator, packaged_validator("record_verification.v1")),
    )


def _identifier(value: str) -> str:
    try:
        return record_id(value)
    except FormatError:
        raise argparse.ArgumentTypeError("valid new record ID required") from None


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--exchange", type=Path, required=True, metavar="DIR")
    parser.add_argument("--root", type=Path, required=True, help="Explicit private record root.")
    parser.add_argument("--id", type=_identifier, required=True, help="New immutable record ID.")
    parser.add_argument("--json", action="store_true", help=f"Emit {SCHEMA} JSON.")
    parser.description = (
        "Audit and retain the exact five baseline exchange files, result or finite diagnostic, "
        "and invocation. Complete malformed input consumes the ID when saved. "
        "Stored-byte integrity is not independent provenance or approval. "
        "Exit 0 means result saved, 2 diagnostic saved, and 3 storage failed."
    )


def _input_error(exc: CaptureError | BaselineProjectionError) -> EvidenceError:
    return EvidenceError(exc.code, exc.reason, exc.location or "/exchange")


def _analysis(captured: dict[str, CapturedBytes], contracts: _Contracts) -> dict[str, Any]:
    try:
        report = project_baseline_result(decode_baseline_documents(captured))
    except (CaptureError, BaselineProjectionError) as exc:
        if exc.code not in {"INVALID_INPUT", "INPUT_LIMIT_EXCEEDED"}:
            raise _input_error(exc) from None
        error = _input_error(exc).payload
        validate(contracts.evidence_error, error, EvidenceError, "INTERNAL_ERROR")
        return {"state": "error", "error": error}
    validate(contracts.result, report, EvidenceError, "INTERNAL_ERROR")
    identity = cast(dict[str, Any], cast(dict[str, Any], report["source"])["identity"])
    members = identity["members"]
    expected = {name: captured[name].sha256 for name in FILES}
    if (
        identity["manifest_raw_hash"] != expected["manifest.json"]
        or members != expected
    ):
        raise EvidenceError(
            "INPUT_BINDING_MISMATCH",
            "BASELINE_CAPTURE_IDENTITY_MISMATCH",
            "/result/source/identity",
        )
    return {"state": "result", "report": report}


def _files(
    captured: dict[str, CapturedBytes], analysis: dict[str, Any], identifier: str
) -> tuple[CapturedRecordFile, ...]:
    result = analysis["state"] == "result"
    document = analysis["report"] if result else analysis["error"]
    try:
        encoded = canonical_bytes(document) + b"\n"
    except CanonicalizationError:
        raise EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR") from None
    if len(encoded) > RECORD_READ_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    invocation = {
        "command": COMMAND,
        "profile": PROFILE,
        "execution_mode": "in_process",
        "record_id": identifier,
        "analysis_state": analysis["state"],
        "analysis_exit_code": 0 if result else 2,
    }
    invocation_bytes = canonical_bytes(invocation) + b"\n"
    if len(invocation_bytes) > INVOCATION_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    return (
        *(
            CapturedRecordFile("source/" + name, "source", captured[name].data)
            for name in FILES
        ),
        CapturedRecordFile(
            "analysis/evidence_result.json" if result else "analysis/error.json",
            "result" if result else "log",
            encoded,
            "application/json",
        ),
        CapturedRecordFile("invocation.json", "log", invocation_bytes, "application/json"),
    )


def _analysis_text(analysis: dict[str, Any]) -> str:
    if analysis["state"] == "error":
        error = analysis["error"]
        return f"Baseline analysis failed: {error['code']} ({error['reason']})."
    return "Descriptive baseline result produced from captured source claims."


def render_human(report: dict[str, object]) -> str:
    return render_record_response(
        report, analysis_text=_analysis_text(cast(dict[str, Any], report["analysis"]))
    )


def run_record_baseline(
    exchange: Path, root: Path, identifier: str, *, as_json: bool = True
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    contracts = _contracts()
    try:
        captured = capture_baseline_files(exchange)
    except CaptureError as exc:
        raise _input_error(exc) from None
    if set(captured) != set(FILES):
        raise EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR")
    if any(
        item.logical_name != name
        or item.size != len(item.data)
        or item.sha256 != digest_bytes(item.data)
        for name, item in captured.items()
    ):
        raise EvidenceError(
            "INPUT_BINDING_MISMATCH", "BASELINE_CAPTURE_IDENTITY_MISMATCH", "/exchange"
        )
    analysis = _analysis(captured, contracts)
    source = {
        "state": "captured",
        "files": [
            {"name": name, "raw_hash": captured[name].sha256, "bytes": captured[name].size}
            for name in FILES
        ],
    }
    envelope: dict[str, Any] = {
        "schema": SCHEMA,
        "command": COMMAND,
        "source": source,
        "analysis": analysis,
        "exit_code": 0 if analysis["state"] == "result" else 2,
    }
    files = _files(captured, analysis, identifier)
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
        kind="goppi.baseline-exchange",
        label="Goppi baseline exchange capture",
        metadata={"profile": PROFILE, "analysis_state": analysis["state"]},
        links=(),
        selection_excluded=None,
        rerun_verification={"status": "not_performed", "reference": None},
        provenance={"verified": False, "attestation_ref": None},
        files=files,
    )
    envelope["storage"] = store_captured_record(
        root, spec, contracts.verification, contracts.record_error
    )
    if envelope["storage"]["state"] == "error":
        envelope["exit_code"] = STORAGE_FAILURE_EXIT
    return envelope


def produce(args: argparse.Namespace) -> dict[str, object]:
    return run_record_baseline(
        cast(Path, args.exchange),
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
