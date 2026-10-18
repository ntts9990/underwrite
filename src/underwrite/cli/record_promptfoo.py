"""Fixed in-process Promptfoo accounting and capture of the same selected bytes."""

from __future__ import annotations

import argparse
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.cli.evidence import (
    REPORT_MAX_BYTES,
    EvidenceError,
    ReportOutput,
    run_cli,
)
from underwrite.cli.local_json import Validator, validate
from underwrite.cli.record_capture import (
    STORAGE_FAILURE_EXIT,
    admit_record_response,
    render_record_response,
    storage_validator,
    store_captured_record,
)
from underwrite.cli.records import OUTPUT_MAX_BYTES, record_error_payload
from underwrite.instrument.evidence.promptfoo_compat import (
    ACCOUNTING_SCHEMA as AUTO_ACCOUNTING_SCHEMA,
)
from underwrite.instrument.ingest.accounting import (
    SOURCE,
    AccountingCaseError,
    audit_promptfoo_bytes,
    read_promptfoo_file,
    validate_version,
)
from underwrite.instrument.ingest.codecs.promptfoo import SUPPORTED_VERSIONS
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import (
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
)

SCHEMA = "promptfoo_record.v1"
AUTO_RECORD_SCHEMA = "promptfoo_record.v2"
COMMAND = "records audit-promptfoo"


@dataclass(frozen=True)
class _Contracts:
    version: str | None
    aggregate: Validator
    storage: Validator
    analysis: Validator
    evidence_error: Validator
    record_error: Validator
    verification: Validator


def _contracts(version: str | None) -> _Contracts:
    aggregate = packaged_validator(AUTO_RECORD_SCHEMA if version is None else SCHEMA)
    storage = storage_validator(aggregate)
    if version is not None:
        packaged_validator("observation.v1")
    analysis_schema = AUTO_ACCOUNTING_SCHEMA if version is None else "promptfoo_accounting.v1"
    return _Contracts(
        version,
        cast(Validator, aggregate),
        storage,
        cast(Validator, packaged_validator(analysis_schema)),
        cast(Validator, packaged_validator("evidence_error.v1")),
        cast(Validator, packaged_validator("record_error.v1")),
        cast(Validator, packaged_validator("record_verification.v1")),
    )


def _check(validator: Validator, value: object) -> None:
    validate(validator, value, EvidenceError, "INTERNAL_ERROR")


def _applied_reader(analysis: dict[str, Any]) -> dict[str, str]:
    """Describe a selected auto reader only after successful analysis."""
    if analysis["state"] != "result":
        return {}
    report = cast(dict[str, Any], analysis["report"])
    source = cast(dict[str, object], report["source"])
    selected = {
        "reader_profile": cast(str, report["reader_profile"]),
        "producer_version_state": cast(str, source["producer_version_state"]),
    }
    if source["producer_version"] is not None:
        selected["producer_version"] = cast(str, source["producer_version"])
    return selected


def _identifier(value: str) -> str:
    try:
        return record_id(value)
    except FormatError:
        raise argparse.ArgumentTypeError("valid new record ID required") from None


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "file",
        type=Path,
        metavar="FILE",
        help="Entire local Promptfoo export to retain.",
    )
    parser.add_argument(
        "--version",
        choices=SUPPORTED_VERSIONS,
        default=None,
        help="Optional strict historical producer version; omitted uses selected SummaryV3 fields.",
    )
    parser.add_argument("--root", type=Path, required=True, help="Explicit private record root.")
    parser.add_argument("--id", type=_identifier, required=True, help="New immutable record ID.")
    parser.add_argument(
        "--json", action="store_true", help="Emit the selected Promptfoo record JSON contract."
    )
    parser.description = (
        "Audit one local Promptfoo export in process and retain the exact complete input, "
        "result or diagnostic, and effective invocation. Malformed complete input is retained "
        "in full, including private source fields; a diagnostic record consumes the ID. "
        "Without --version, selected SummaryV3 fields are analyzed and a producer version is "
        "only an unverified declaration when present. No producer or subprocess is run. "
        "Exit 0 means result saved, 2 analysis failed but diagnostic saved, and 3 storage failed."
    )


def _analyze(
    path: Path, version: str | None
) -> tuple[bytes | None, dict[str, object], dict[str, Any]]:
    raw: bytes | None = None
    source: dict[str, object] = {"state": "unavailable"}
    try:
        raw = read_promptfoo_file(path)
        source = {"state": "captured", "raw_hash": digest_bytes(raw), "bytes": len(raw)}
        report = audit_promptfoo_bytes(raw, version=version)
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
    _check(contracts.analysis if result else contracts.evidence_error, document)
    encoded = canonical_bytes(document) + b"\n"
    if len(encoded) > REPORT_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    arguments = {
        "source_path_display": unicodedata.normalize("NFC", str(path)),
        "root_display": unicodedata.normalize("NFC", str(root)),
        "record_id": identifier,
    }
    if contracts.version is None:
        arguments["selection_mode"] = "auto"
        arguments.update(_applied_reader(analysis))
    elif contracts.version != SOURCE[1]:
        arguments["format_version"] = contracts.version
    invocation = {
        "command": COMMAND,
        "execution_mode": "in_process",
        "arguments": arguments,
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


def render_human(report: dict[str, object]) -> str:
    return render_record_response(
        report, analysis_text=_analysis_text(cast(dict[str, Any], report["analysis"]))
    )


def run_record_promptfoo(
    path: Path, root: Path, identifier: str, *, as_json: bool = True, version: str | None = None
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    if version is not None:
        try:
            validate_version(version)
        except AccountingCaseError as exc:
            raise EvidenceError(exc.code, exc.reason, exc.location) from None
    contracts = _contracts(version)
    raw, source, analysis = _analyze(path, version)
    envelope: dict[str, Any] = {
        "schema": AUTO_RECORD_SCHEMA if version is None else SCHEMA,
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
    metadata = {
        "format": SOURCE[0],
        "analysis_state": analysis["state"],
    }
    if version is None:
        metadata["selection_mode"] = "auto"
        metadata.update(_applied_reader(analysis))
    else:
        metadata["format_version"] = version
    spec = CapturedRecordSpec(
        record_id=identifier,
        kind="promptfoo.accounting",
        label="Promptfoo accounting capture",
        metadata=metadata,
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
    return run_record_promptfoo(
        cast(Path, args.file),
        cast(Path, args.root),
        cast(str, args.id),
        as_json=cast(bool, args.json),
        version=cast(str | None, args.version),
    )


def main(argv: list[str]) -> int:
    return run_cli(
        COMMAND,
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: AUTO_RECORD_SCHEMA if args.version is None else SCHEMA,
            max_bytes=OUTPUT_MAX_BYTES,
            human=render_human,
            exit_status=lambda report: cast(int, report["exit_code"]),
        ),
    )
