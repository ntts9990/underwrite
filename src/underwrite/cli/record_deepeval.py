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
from underwrite.cli.audit_deepeval import AUTO_ORDINARY_HUMAN_LIMIT, render_trace_human
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
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_SCHEMA as AUTO_ACCOUNTING_SCHEMA,
)
from underwrite.instrument.evidence.deepeval_compat import TRACE_SCHEMA as AUTO_TRACE_INDEX_SCHEMA
from underwrite.instrument.evidence.deepeval_trace import (
    RULE_PROFILE as TRACE_RULE_PROFILE,
)
from underwrite.instrument.evidence.deepeval_trace import (
    SCHEMA as TRACE_INDEX_SCHEMA,
)
from underwrite.instrument.evidence.deepeval_trace import (
    SOURCE_VERSION as TRACE_SOURCE_VERSION,
)
from underwrite.instrument.ingest.deepeval_accounting import (
    PROFILES,
    SOURCE,
    DeepevalFileError,
    DeepevalProfile,
    audit_deepeval_bytes,
    profile_for,
    read_deepeval_file,
)
from underwrite.instrument.ingest.deepeval_compat import View, audit_deepeval_compatible_bytes
from underwrite.instrument.ingest.deepeval_trace import audit_deepeval_trace_bytes
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import (
    RECORD_READ_MAX_BYTES,
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
)

SCHEMA = "deepeval_record.v1"
TRACE_RECORD_SCHEMA = "deepeval_trace_record.v1"
AUTO_RECORD_SCHEMA = "deepeval_record.v3"
AUTO_TRACE_RECORD_SCHEMA = "deepeval_trace_record.v2"
COMMAND = "records audit-deepeval"
TRACE_VIEW = "trace-index"


@dataclass(frozen=True)
class _Selection:
    profile: DeepevalProfile | None
    view: str
    record_schema: str
    analysis_schema: str
    result_member: str
    kind: str
    label: str


def _selection(version: str | None, view: str) -> _Selection:
    if version is None:
        if view == "accounting":
            return _Selection(
                None,
                view,
                AUTO_RECORD_SCHEMA,
                AUTO_ACCOUNTING_SCHEMA,
                "analysis/accounting.json",
                "deepeval.accounting",
                "DeepEval accounting capture",
            )
        if view == TRACE_VIEW:
            return _Selection(
                None,
                view,
                AUTO_TRACE_RECORD_SCHEMA,
                AUTO_TRACE_INDEX_SCHEMA,
                "analysis/trace-index.json",
                "deepeval.trace-index",
                "DeepEval trace-index capture",
            )
        raise EvidenceError("UNSUPPORTED_FORMAT", "DEEPEVAL_RECORD_VIEW_UNSUPPORTED", "/view")
    try:
        profile = profile_for(version)
    except DeepevalFileError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None
    if view == "accounting":
        return _Selection(
            profile,
            view,
            profile.record_schema,
            profile.accounting_schema,
            "analysis/accounting.json",
            "deepeval.accounting",
            "DeepEval accounting capture",
        )
    if view == TRACE_VIEW:
        if version != TRACE_SOURCE_VERSION:
            raise EvidenceError(
                "UNSUPPORTED_FORMAT", "DEEPEVAL_TRACE_VERSION_UNSUPPORTED", "/version"
            )
        return _Selection(
            profile,
            view,
            TRACE_RECORD_SCHEMA,
            TRACE_INDEX_SCHEMA,
            "analysis/trace-index.json",
            "deepeval.trace-index",
            "DeepEval trace-index capture",
        )
    raise EvidenceError("UNSUPPORTED_FORMAT", "DEEPEVAL_RECORD_VIEW_UNSUPPORTED", "/view")


@dataclass(frozen=True)
class _Contracts:
    selection: _Selection
    aggregate: Validator
    storage: Validator
    analysis: Validator
    evidence_error: Validator
    record_error: Validator
    verification: Validator


def _contracts(selection: _Selection) -> _Contracts:
    aggregate = packaged_validator(selection.record_schema)
    schema = cast(dict[str, Any], aggregate.schema)
    storage = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/storage"})
    if selection.view == "accounting" and selection.profile is not None:
        packaged_validator("observation.v1")
    return _Contracts(
        selection,
        cast(Validator, aggregate),
        cast(Validator, storage),
        cast(Validator, packaged_validator(selection.analysis_schema)),
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
        help="Entire local DeepEval TestRun JSON to retain.",
    )
    parser.add_argument(
        "--version",
        choices=tuple(PROFILES),
        default=None,
        help="Optional historical source version; omitted selects local automatic rules.",
    )
    parser.add_argument(
        "--bindings",
        type=Path,
        default=None,
        help=(
            "Owner-asserted metric pack: format-only v2 without --version; "
            "v1 with --version 4.2.8."
        ),
    )
    parser.add_argument(
        "--view",
        choices=("accounting", TRACE_VIEW),
        default="accounting",
        help="Select ordinary accounting or native trace indexing.",
    )
    parser.add_argument("--root", type=Path, required=True, help="Explicit private record root.")
    parser.add_argument("--id", type=_identifier, required=True, help="New immutable record ID.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the selected DeepEval record JSON contract.",
    )
    parser.description = (
        "Audit one local DeepEval export in process and retain the exact complete input, "
        "result or diagnostic, and effective invocation. Malformed complete input is retained "
        "in full; a diagnostic record consumes the ID. No producer or subprocess is run. "
        "The trace-index view retains the complete private source, even though its index "
        "omits source text and identifiers. "
        "Exit 0 means result saved, 2 analysis failed but diagnostic saved, and 3 storage failed."
    )


def _analyze(
    path: Path, selection: _Selection
) -> tuple[bytes | None, dict[str, object], dict[str, Any]]:
    raw: bytes | None = None
    source: dict[str, object] = {"state": "unavailable"}
    try:
        raw = read_deepeval_file(path)
        source = {"state": "captured", "raw_hash": digest_bytes(raw), "bytes": len(raw)}
        if selection.profile is None:
            report = audit_deepeval_compatible_bytes(raw, view=cast(View, selection.view))
        else:
            audit = (
                audit_deepeval_trace_bytes if selection.view == TRACE_VIEW else audit_deepeval_bytes
            )
            report = audit(raw, version=selection.profile.format_version)
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
    _check(contracts.analysis if result else contracts.evidence_error, document)
    encoded = canonical_bytes(document) + b"\n"
    if len(encoded) > RECORD_READ_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    arguments = {
        "source_path_display": unicodedata.normalize("NFC", str(path)),
        "root_display": unicodedata.normalize("NFC", str(root)),
        "record_id": identifier,
    }
    if contracts.selection.profile is None:
        arguments["selection_mode"] = "auto"
        if result:
            arguments["reader_profile"] = document["reader_profile"]
    elif contracts.selection.profile.format_version != SOURCE[1]:
        arguments["format_version"] = contracts.selection.profile.format_version
    if contracts.selection.view == TRACE_VIEW:
        arguments["view"] = TRACE_VIEW
        if contracts.selection.profile is not None:
            arguments["rule_profile"] = TRACE_RULE_PROFILE
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
            contracts.selection.result_member if result else "analysis/error.json",
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


def _analysis_text(analysis: dict[str, Any], *, trace: bool = False) -> str:
    if analysis["state"] == "error":
        error = analysis["error"]
        return f"Analysis failed during {analysis['phase']}: {error['code']} ({error['reason']})."
    if trace:
        return render_trace_human(analysis["report"])
    counts = analysis["report"]["counts"]
    metrics = counts["metrics"]
    text = (
        f"Accounting result: {counts['cases']['total']} cases; {metrics['total']} metrics; "
        f"scored {metrics['scored']}; evaluator errors {metrics['error']}; "
        f"unscored {metrics['unscored']}; numeric zeros {metrics['observed_zero']}. "
        "Planned evaluations unknown; source claims unverified; counts do not establish approval."
    )
    return (
        text + " " + AUTO_ORDINARY_HUMAN_LIMIT
        if analysis["report"]["schema"] == AUTO_ACCOUNTING_SCHEMA
        else text
    )


def render_human(report: dict[str, object]) -> str:
    return render_record_response(
        report,
        analysis_text=_analysis_text(
            cast(dict[str, Any], report["analysis"]),
            trace=report["schema"] in (TRACE_RECORD_SCHEMA, AUTO_TRACE_RECORD_SCHEMA),
        ),
    )


def run_record_deepeval(
    path: Path,
    root: Path,
    identifier: str,
    *,
    as_json: bool = True,
    version: str | None = None,
    view: str = "accounting",
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    selection = _selection(version, view)
    contracts = _contracts(selection)
    raw, source, analysis = _analyze(path, selection)
    envelope: dict[str, Any] = {
        "schema": selection.record_schema,
        "command": COMMAND,
        "source": source,
        "analysis": analysis,
        "exit_code": 0 if analysis["state"] == "result" else 2,
    }
    if selection.profile is not None and selection.profile.format_version != SOURCE[1]:
        envelope["source_format_version"] = selection.profile.format_version
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
            analysis_text=_analysis_text(analysis, trace=selection.view == TRACE_VIEW),
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
        analysis_text=_analysis_text(analysis, trace=selection.view == TRACE_VIEW),
        as_json=as_json,
        max_bytes=OUTPUT_MAX_BYTES,
    )
    metadata = {"format": SOURCE[0], "analysis_state": analysis["state"]}
    if selection.profile is None:
        metadata["selection_mode"] = "auto"
        if analysis["state"] == "result":
            metadata["reader_profile"] = analysis["report"]["reader_profile"]
    else:
        metadata["format_version"] = selection.profile.format_version
    if selection.view == TRACE_VIEW:
        metadata["view"] = TRACE_VIEW
        if selection.profile is not None:
            metadata["rule_profile"] = TRACE_RULE_PROFILE
    spec = CapturedRecordSpec(
        record_id=identifier,
        kind=selection.kind,
        label=selection.label,
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
    if args.view == TRACE_VIEW:
        if args.bindings is not None:
            raise EvidenceError(
                "UNSUPPORTED_FORMAT", "DEEPEVAL_TRACE_BINDINGS_UNSUPPORTED", "/bindings"
            )
        return run_record_deepeval(
            cast(Path, args.file),
            cast(Path, args.root),
            cast(str, args.id),
            as_json=cast(bool, args.json),
            version=cast(str | None, args.version),
            view=TRACE_VIEW,
        )
    if args.bindings is not None:
        return record_deepeval_bound.run_record_bound_deepeval(
            cast(Path, args.file),
            cast(Path, args.bindings),
            cast(Path, args.root),
            cast(str, args.id),
            as_json=cast(bool, args.json),
            version=cast(str | None, args.version),
        )
    return run_record_deepeval(
        cast(Path, args.file),
        cast(Path, args.root),
        cast(str, args.id),
        as_json=cast(bool, args.json),
        version=cast(str | None, args.version),
    )


def _output_schema(args: argparse.Namespace) -> str:
    if args.view == TRACE_VIEW:
        if args.bindings is not None:
            raise EvidenceError(
                "UNSUPPORTED_FORMAT", "DEEPEVAL_TRACE_BINDINGS_UNSUPPORTED", "/bindings"
            )
        return _selection(cast(str | None, args.version), TRACE_VIEW).record_schema
    if args.bindings is not None:
        return (
            record_deepeval_bound.AUTO_SCHEMA
            if args.version is None
            else record_deepeval_bound.SCHEMA
        )
    return _selection(cast(str | None, args.version), "accounting").record_schema


def main(argv: list[str]) -> int:
    return run_cli(
        COMMAND,
        argv,
        configure,
        produce,
        ReportOutput(
            _output_schema,
            max_bytes=OUTPUT_MAX_BYTES,
            human=lambda report: (
                record_deepeval_bound.render_human(report)
                if report["schema"]
                in (record_deepeval_bound.SCHEMA, record_deepeval_bound.AUTO_SCHEMA)
                else render_human(report)
            ),
            exit_status=lambda report: cast(int, report["exit_code"]),
        ),
    )
