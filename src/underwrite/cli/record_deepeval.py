"""Card: record-reuse | Paper: deepeval_record.v1 | Stage: storage-capture.

In-process accounting and capture of the same selected bytes.
"""

from __future__ import annotations

import argparse
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.cli import record_deepeval_bound
from underwrite.cli.evidence import (
    EvidenceError,
    ReportOutput,
    run_cli,
)
from underwrite.cli.local_json import Validator, validate
from underwrite.cli.record_capture import (
    STORAGE_FAILURE_EXIT,
    admit_record_response,
    render_record_response,
    store_captured_record,
)
from underwrite.cli.records import OUTPUT_MAX_BYTES, record_error_payload
from underwrite.instrument.ingest.deepeval_accounting import (
    PROFILES,
    SOURCE,
    DeepevalFileError,
    DeepevalProfile,
    audit_deepeval_bytes,
    profile_for,
    read_deepeval_file,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import (
    RECORD_READ_MAX_BYTES,
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
)

SCHEMA = "deepeval_record.v1"
COMMAND = "records audit-deepeval"


@dataclass(frozen=True)
class _Contracts:
    profile: DeepevalProfile
    aggregate: Validator
    storage: Validator
    accounting: Validator
    evidence_error: Validator
    record_error: Validator
    verification: Validator


def _contracts(profile: DeepevalProfile) -> _Contracts:
    aggregate = packaged_validator(profile.record_schema)
    schema = cast(dict[str, Any], aggregate.schema)
    storage = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/storage"})
    packaged_validator("observation.v1")
    return _Contracts(
        profile,
        cast(Validator, aggregate),
        cast(Validator, storage),
        cast(Validator, packaged_validator(profile.accounting_schema)),
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
        "file",
        type=Path,
        metavar="FILE",
        help="Entire DeepEval export for the selected version to retain.",
    )
    parser.add_argument(
        "--version",
        choices=tuple(PROFILES),
        default=SOURCE[1],
        help="Explicit source version; no detection or fallback.",
    )
    parser.add_argument(
        "--bindings",
        type=Path,
        default=None,
        help="Owner-asserted DeepEval 4.2.8 metric binding pack to retain with the source.",
    )
    parser.add_argument("--root", type=Path, required=True, help="Explicit private record root.")
    parser.add_argument("--id", type=_identifier, required=True, help="New immutable record ID.")
    parser.add_argument(
        "--json", action="store_true", help="Emit the selected deepeval_record JSON contract."
    )
    parser.description = (
        "Audit one local DeepEval export in process and retain the exact complete input, "
        "result or diagnostic, and effective invocation. Malformed complete input is retained "
        "in full; a diagnostic record consumes the ID. No producer or subprocess is run. "
        "Exit 0 means result saved, 2 analysis failed but diagnostic saved, and 3 storage failed."
    )


def _analyze(
    path: Path, profile: DeepevalProfile
) -> tuple[bytes | None, dict[str, object], dict[str, Any]]:
    raw: bytes | None = None
    source: dict[str, object] = {"state": "unavailable"}
    try:
        raw = read_deepeval_file(path)
        source = {"state": "captured", "raw_hash": digest_bytes(raw), "bytes": len(raw)}
        report = audit_deepeval_bytes(raw, version=profile.format_version)
        if report["raw_hash"] != source["raw_hash"]:
            raise EvidenceError("INTERNAL_ERROR", "RAW_IDENTITY_MISMATCH")
        analysis: dict[str, Any] = {"state": "result", "report": report}
    except DeepevalFileError as exc:
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
    if len(encoded) > RECORD_READ_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    arguments = {
        "source_path_display": unicodedata.normalize("NFC", str(path)),
        "root_display": unicodedata.normalize("NFC", str(root)),
        "record_id": identifier,
    }
    if contracts.profile.format_version != SOURCE[1]:
        arguments["format_version"] = contracts.profile.format_version
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
        files.insert(0, CapturedRecordFile("source/deepeval.bin", "source", raw))
    return tuple(files)


def _analysis_text(analysis: dict[str, Any]) -> str:
    if analysis["state"] == "error":
        error = analysis["error"]
        return f"Analysis failed during {analysis['phase']}: {error['code']} ({error['reason']})."
    counts = analysis["report"]["counts"]
    metrics = counts["metrics"]
    return (
        f"Accounting result: {counts['cases']['total']} cases; {metrics['total']} metrics; "
        f"scored {metrics['scored']}; evaluator errors {metrics['error']}; "
        f"unscored {metrics['unscored']}; numeric zeros {metrics['observed_zero']}. "
        "Planned evaluations unknown; source claims unverified; counts do not establish approval."
    )


def render_human(report: dict[str, object]) -> str:
    return render_record_response(
        report, analysis_text=_analysis_text(cast(dict[str, Any], report["analysis"]))
    )


def run_record_deepeval(
    path: Path, root: Path, identifier: str, *, as_json: bool = True, version: str = SOURCE[1]
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    try:
        profile = profile_for(version)
    except DeepevalFileError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None
    contracts = _contracts(profile)
    raw, source, analysis = _analyze(path, profile)
    envelope: dict[str, Any] = {
        "schema": profile.record_schema,
        "command": COMMAND,
        "source": source,
        "analysis": analysis,
        "exit_code": 0 if analysis["state"] == "result" else 2,
    }
    if profile.format_version != SOURCE[1]:
        envelope["source_format_version"] = profile.format_version
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
    spec = CapturedRecordSpec(
        record_id=identifier,
        kind="deepeval.accounting",
        label="DeepEval accounting capture",
        metadata={
            "format": SOURCE[0],
            "format_version": profile.format_version,
            "analysis_state": analysis["state"],
        },
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
    if args.bindings is not None:
        return record_deepeval_bound.run_record_bound_deepeval(
            cast(Path, args.file),
            cast(Path, args.bindings),
            cast(Path, args.root),
            cast(str, args.id),
            as_json=cast(bool, args.json),
            version=cast(str, args.version),
        )
    return run_record_deepeval(
        cast(Path, args.file),
        cast(Path, args.root),
        cast(str, args.id),
        as_json=cast(bool, args.json),
        version=cast(str, args.version),
    )


def main(argv: list[str]) -> int:
    return run_cli(
        COMMAND,
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: record_deepeval_bound.SCHEMA
            if args.bindings is not None
            else profile_for(cast(str, args.version)).record_schema,
            max_bytes=OUTPUT_MAX_BYTES,
            human=lambda report: record_deepeval_bound.render_human(report)
            if report["schema"] == record_deepeval_bound.SCHEMA
            else render_human(report),
            exit_status=lambda report: cast(int, report["exit_code"]),
        ),
    )
