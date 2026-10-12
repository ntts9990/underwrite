"""Fixed in-process Promptfoo accounting and capture of the same selected bytes."""

from __future__ import annotations

import argparse
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.cli.evidence import (
    REPORT_MAX_BYTES,
    EvidenceError,
    ReportOutput,
    output_line_size,
    run_cli,
)
from underwrite.cli.local_json import Validator, validate
from underwrite.cli.records import OUTPUT_MAX_BYTES, record_error_candidates, record_error_payload
from underwrite.instrument.ingest.accounting import (
    SOURCE,
    AccountingCaseError,
    audit_promptfoo_bytes,
    read_promptfoo_file,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import (
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
    save_captured_record,
    verify_record,
)

SCHEMA = "promptfoo_record.v1"
COMMAND = "records audit-promptfoo"
_STORAGE_FAILURE_EXIT = 3


@dataclass(frozen=True)
class _Contracts:
    aggregate: Validator
    storage: Validator
    accounting: Validator
    evidence_error: Validator
    record_error: Validator
    verification: Validator


def _contracts() -> _Contracts:
    aggregate = packaged_validator("promptfoo_record.v1")
    schema = cast(dict[str, Any], aggregate.schema)
    storage = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/storage"})
    packaged_validator("observation.v1")
    return _Contracts(
        cast(Validator, aggregate),
        cast(Validator, storage),
        cast(Validator, packaged_validator("promptfoo_accounting.v1")),
        cast(Validator, packaged_validator("evidence_error.v1")),
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
        "file", type=Path, metavar="FILE", help="Entire Promptfoo 0.123.0 export to retain."
    )
    parser.add_argument("--root", type=Path, required=True, help="Explicit private record root.")
    parser.add_argument("--id", type=_identifier, required=True, help="New immutable record ID.")
    parser.add_argument("--json", action="store_true", help="Emit promptfoo_record.v1 JSON.")
    parser.description = (
        "Audit one local Promptfoo export in process and retain the exact complete input, "
        "result or diagnostic, and effective invocation. Malformed complete input is retained "
        "in full; a diagnostic record consumes the ID. No producer or subprocess is run. "
        "Exit 0 means result saved, 2 analysis failed but diagnostic saved, and 3 storage failed."
    )


def _analyze(path: Path) -> tuple[bytes | None, dict[str, object], dict[str, Any]]:
    raw: bytes | None = None
    source: dict[str, object] = {"state": "unavailable"}
    try:
        raw = read_promptfoo_file(path)
        source = {"state": "captured", "raw_hash": digest_bytes(raw), "bytes": len(raw)}
        report = audit_promptfoo_bytes(raw)
        if report["raw_hash"] != source["raw_hash"]:
            raise EvidenceError("INTERNAL_ERROR", "RAW_IDENTITY_MISMATCH")
        analysis: dict[str, Any] = {"state": "result", "report": report}
    except AccountingCaseError as exc:
        if exc.code == "SCHEMA_CONFIGURATION_ERROR":
            raise EvidenceError(exc.code, exc.reason, exc.location) from None
        analysis = {
            "state": "error",
            "phase": "acquisition" if raw is None else "analysis",
            "error": EvidenceError(exc.code, exc.reason, exc.location).payload,
        }
    except (EvidenceError, SchemaConfigurationError):
        raise
    except Exception:
        analysis = {
            "state": "error",
            "phase": "acquisition" if raw is None else "analysis",
            "error": EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR").payload,
        }
    return raw, source, analysis


def _files(
    path: Path,
    root: Path,
    identifier: str,
    raw: bytes | None,
    analysis: dict[str, Any],
    contracts: _Contracts,
) -> tuple[CapturedRecordFile, ...]:
    result = analysis["state"] == "result"
    document = analysis["report"] if result else analysis["error"]
    _check(contracts.accounting if result else contracts.evidence_error, document)
    encoded = canonical_bytes(document) + b"\n"
    if len(encoded) > REPORT_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    invocation = {
        "command": COMMAND,
        "execution_mode": "in_process",
        "arguments": {
            "source_path_display": unicodedata.normalize("NFC", str(path)),
            "root_display": unicodedata.normalize("NFC", str(root)),
            "record_id": identifier,
        },
        "analysis_state": analysis["state"],
        "analysis_exit_code": 0 if result else 2,
        "analysis_exit_code_scope": "component_contract_not_os_process",
        "aggregate_outcome_location": "command_output",
    }
    files = [
        CapturedRecordFile(
            "analysis/accounting.json" if result else "analysis/error.json",
            "result" if result else "log",
            encoded,
            "application/json",
        ),
        CapturedRecordFile(
            "invocation.json", "log", canonical_bytes(invocation) + b"\n", "application/json"
        ),
    ]
    if raw is not None:
        files.insert(0, CapturedRecordFile("source/promptfoo.bin", "source", raw))
    return tuple(files)


def _saved(identifier: str, digest: str, count: int) -> dict[str, object]:
    return {
        "state": "saved",
        "record_id": identifier,
        "manifest_digest": digest,
        "verification": {
            "schema": "record_verification.v1",
            "record_id": identifier,
            "manifest_digest": digest,
            "valid": True,
            "files_checked": count,
            "links_checked": 0,
            "findings": [],
            "verification_scope": "stored_bytes_and_direct_links",
        },
    }


def _analysis_text(analysis: dict[str, Any]) -> str:
    if analysis["state"] == "error":
        error = analysis["error"]
        return f"Analysis failed during {analysis['phase']}: {error['code']} ({error['reason']})."
    report = analysis["report"]
    return (
        f"Accounting result: {report['declared_relation']}; "
        f"declared {report['producer_stats']['total']}, observed {report['row_tallies']['total']}. "
        "Counts do not establish planned inventory or approval."
    )


def _storage_text(storage: dict[str, Any]) -> str:
    if storage["state"] == "saved":
        return f"Saved and verified record: {storage['record_id']} ({storage['manifest_digest']})."
    error = storage["error"]
    text = f"Storage failed: {error['code']} ({error['reason']}); next: {error['next_action']}."
    known = storage["published_record"]
    if known is not None:
        return (
            text + f" Returned record identity: {known['record_id']} ({known['manifest_digest']})."
        )
    return text + " No record identity returned; a record may still exist."


def render_human(report: dict[str, object]) -> str:
    return (
        _analysis_text(cast(dict[str, Any], report["analysis"]))
        + "\n"
        + _storage_text(cast(dict[str, Any], report["storage"]))
        + f"\nExit: {report['exit_code']}."
    )


def _admit_response(
    envelope: dict[str, Any], contracts: _Contracts, identifier: str, count: int, as_json: bool
) -> None:
    digest = "sha256:" + "0" * 64
    representative = envelope.get("storage") or _saved(identifier, digest, count)
    envelope["storage"] = representative
    _check(contracts.aggregate, envelope)
    base_size = output_line_size(canonical_bytes(envelope))
    storage_size = len(canonical_bytes(representative))
    maximum = base_size
    human_prefix = _analysis_text(envelope["analysis"]) + "\n"
    human_maximum = output_line_size(render_human(envelope))
    identity = {"record_id": identifier, "manifest_digest": digest}
    for error in record_error_candidates():
        for published in (None, identity):
            candidate = {"state": "error", "error": error, "published_record": published}
            _check(contracts.storage, candidate)
            maximum = max(maximum, base_size - storage_size + len(canonical_bytes(candidate)))
            human_maximum = max(
                human_maximum,
                output_line_size(
                    human_prefix + _storage_text(candidate) + f"\nExit: {_STORAGE_FAILURE_EXIT}."
                ),
            )
    if (maximum if as_json else human_maximum) > OUTPUT_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")


def _store(root: Path, spec: CapturedRecordSpec, contracts: _Contracts) -> dict[str, Any]:
    known: dict[str, object] | None = None
    try:
        snapshot = save_captured_record(root, spec)
        known = {"record_id": spec.record_id, "manifest_digest": snapshot.digest}
        verification = verify_record(root, spec.record_id)
        _check(contracts.verification, verification)
        expected = _saved(spec.record_id, snapshot.digest, len(spec.files))
        if verification != expected["verification"]:
            raise RecordError("STORE_IO_ERROR")
        return expected
    except RecordError as exc:
        error = record_error_payload(exc)
    except Exception:
        error = record_error_payload(RecordError("INTERNAL_ERROR"))
    _check(contracts.record_error, error)
    return {"state": "error", "error": error, "published_record": known}


def run_record_promptfoo(
    path: Path, root: Path, identifier: str, *, as_json: bool = True
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    contracts = _contracts()
    raw, source, analysis = _analyze(path)
    envelope: dict[str, Any] = {
        "schema": SCHEMA,
        "command": COMMAND,
        "source": source,
        "analysis": analysis,
        "exit_code": 0 if analysis["state"] == "result" else 2,
    }
    try:
        files = _files(path, root, identifier, raw, analysis, contracts)
    except CanonicalizationError:
        envelope["storage"] = {
            "state": "error",
            "error": record_error_payload(RecordError("INVALID_SPEC", "/spec/files")),
            "published_record": None,
        }
        envelope["exit_code"] = _STORAGE_FAILURE_EXIT
        _admit_response(envelope, contracts, identifier, 0, as_json)
        return envelope
    _admit_response(envelope, contracts, identifier, len(files), as_json)
    spec = CapturedRecordSpec(
        record_id=identifier,
        kind="promptfoo.accounting",
        label="Promptfoo accounting capture",
        metadata={
            "format": SOURCE[0],
            "format_version": SOURCE[1],
            "analysis_state": analysis["state"],
        },
        links=(),
        selection_excluded=None,
        rerun_verification={"status": "not_performed", "reference": None},
        provenance={"verified": False, "attestation_ref": None},
        files=files,
    )
    envelope["storage"] = _store(root, spec, contracts)
    if envelope["storage"]["state"] == "error":
        envelope["exit_code"] = _STORAGE_FAILURE_EXIT
    return envelope


def produce(args: argparse.Namespace) -> dict[str, object]:
    return run_record_promptfoo(
        cast(Path, args.file),
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
