"""Card: record-reuse | Paper: langfuse_record.v2 | Stage: storage-capture.

In-process analysis and capture of the same two selected byte buffers.
"""

from __future__ import annotations

import argparse
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

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
    storage_validator,
    store_captured_record,
)
from underwrite.cli.records import OUTPUT_MAX_BYTES, record_error_payload
from underwrite.instrument.evidence.langfuse_compat import AUTO_PROFILE
from underwrite.instrument.ingest.langfuse_accounting import (
    LangfuseFileError,
    audit_langfuse_bytes,
    output_schema,
    read_langfuse_file,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import (
    RECORD_READ_MAX_BYTES,
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
)

AUTO_RECORD_SCHEMA = "langfuse_record.v2"
ROW_RECORD_SCHEMA = "langfuse_row_record.v2"
type View = Literal["accounting", "row-index"]
COMMAND = "records audit-langfuse"


def record_schema(view: str = "accounting") -> str:
    """Select a current record contract for the requested analysis view."""
    if view == "accounting":
        return AUTO_RECORD_SCHEMA
    if view == "row-index":
        return ROW_RECORD_SCHEMA
    raise EvidenceError("UNSUPPORTED_FORMAT", "LANGFUSE_RECORD_VIEW_UNSUPPORTED", "/view")


@dataclass(frozen=True)
class _Contracts:
    view: View
    aggregate: Validator
    storage: Validator
    analysis: Validator
    evidence_error: Validator
    record_error: Validator
    verification: Validator


def _contracts(view: View = "accounting") -> _Contracts:
    aggregate = packaged_validator(record_schema(view))
    if view == "row-index":
        packaged_validator(output_schema("accounting"))
    storage = storage_validator(aggregate)
    return _Contracts(
        view,
        cast(Validator, aggregate),
        storage,
        cast(Validator, packaged_validator(output_schema(view))),
        cast(Validator, packaged_validator("evidence_error.v1")),
        cast(Validator, packaged_validator("record_error.v1")),
        cast(Validator, packaged_validator("record_verification.v1")),
    )


def _check(validator: Validator, value: object) -> None:
    validate(validator, value, EvidenceError, "INTERNAL_ERROR")


def _applied_reader(analysis: dict[str, Any]) -> dict[str, str]:
    """Only successful auto analysis has an applied reader family."""
    if analysis["state"] != "result":
        return {}
    report = cast(dict[str, Any], analysis["report"])
    return {"reader_profile": cast(str, report["reader_profile"])}


def _identifier(value: str) -> str:
    try:
        return record_id(value)
    except FormatError:
        raise argparse.ArgumentTypeError("valid new record ID required") from None


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--observations", type=Path, required=True, help="Entire observations export to retain."
    )
    parser.add_argument(
        "--scores", type=Path, required=True, help="Entire scores export to retain."
    )
    parser.add_argument(
        "--view",
        choices=("accounting", "row-index"),
        default="accounting",
        help=(
            "Accounting (default) or a content-minimized row index; "
            "both retain whole private sources."
        ),
    )
    parser.add_argument("--root", type=Path, required=True, help="Explicit private record root.")
    parser.add_argument("--id", type=_identifier, required=True, help="New immutable record ID.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the selected Langfuse record JSON contract.",
    )
    parser.description = (
        "Audit two selected local Langfuse files in process and retain the exact complete input, "
        "result or diagnostic, and effective invocation. Malformed complete input is retained "
        "in full; a diagnostic record consumes the ID. No producer or subprocess is run. "
        "Reader family is selected from both captured files; release "
        "identity remains unknown. Both views retain whole private files, including possible "
        "headers and text. Row-index minimizes analysis only, not the retained originals. "
        "Exit 0 means result saved, 2 analysis failed but diagnostic saved, and 3 storage failed."
    )


def _acquire(
    paths: dict[str, Path],
) -> tuple[dict[str, bytes], dict[str, object], list[dict[str, object]]]:
    captured: dict[str, bytes] = {}
    sources: dict[str, object] = {}
    errors: list[dict[str, object]] = []
    for role, path in paths.items():
        try:
            raw = read_langfuse_file(path, role)
            captured[role] = raw
            sources[role] = {"state": "captured", "raw_hash": digest_bytes(raw), "bytes": len(raw)}
            continue
        except LangfuseFileError as exc:
            if exc.code == "SCHEMA_CONFIGURATION_ERROR":
                raise EvidenceError(exc.code, exc.reason, exc.location) from None
            error = EvidenceError(exc.code, exc.reason, exc.location).payload
        except Exception:
            error = EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR", "/" + role).payload
        sources[role] = {"state": "unavailable", "error": error}
        errors.append(error)
    return captured, sources, errors


def _analyze(
    paths: dict[str, Path], view: View = "accounting"
) -> tuple[dict[str, bytes], dict[str, object], dict[str, Any]]:
    captured, sources, errors = _acquire(paths)
    if errors:
        return captured, sources, {"state": "error", "phase": "acquisition", "error": errors[0]}
    try:
        report = audit_langfuse_bytes(captured["observations"], captured["scores"], view=view)
        identities = cast(dict[str, dict[str, object]], report["sources"])
        if any(identities[role]["raw_hash"] != digest_bytes(raw) for role, raw in captured.items()):
            raise EvidenceError("INTERNAL_ERROR", "RAW_IDENTITY_MISMATCH")
        analysis: dict[str, Any] = {"state": "result", "report": report}
    except LangfuseFileError as exc:
        if exc.code == "SCHEMA_CONFIGURATION_ERROR":
            raise EvidenceError(exc.code, exc.reason, exc.location) from None
        analysis = {
            "state": "error",
            "phase": "analysis",
            "error": EvidenceError(exc.code, exc.reason, exc.location).payload,
        }
    except (EvidenceError, SchemaConfigurationError):
        raise
    except Exception:
        analysis = {
            "state": "error",
            "phase": "analysis",
            "error": EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR").payload,
        }
    return captured, sources, analysis


def _files(
    paths: dict[str, Path],
    root: Path,
    identifier: str,
    captured: dict[str, bytes],
    envelope: dict[str, Any],
    contracts: _Contracts,
) -> tuple[CapturedRecordFile, ...]:
    analysis = envelope["analysis"]
    result = analysis["state"] == "result"
    document = analysis["report"] if result else analysis["error"]
    _check(contracts.analysis if result else contracts.evidence_error, document)
    encoded = canonical_bytes(document) + b"\n"
    if len(encoded) > RECORD_READ_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    invocation: dict[str, Any] = {
        "command": COMMAND,
        "execution_mode": "in_process",
        "arguments": {
            "observations_path_display": unicodedata.normalize("NFC", str(paths["observations"])),
            "scores_path_display": unicodedata.normalize("NFC", str(paths["scores"])),
            "root_display": unicodedata.normalize("NFC", str(root)),
            "record_id": identifier,
        },
        "sources": envelope["sources"],
        "analysis_state": analysis["state"],
        "analysis_exit_code": 0 if result else 2,
        "analysis_exit_code_scope": "component_contract_not_os_process",
        "aggregate_outcome_location": "command_output",
        "selection_mode": AUTO_PROFILE,
        **_applied_reader(analysis),
    }
    if contracts.view == "row-index":
        invocation["arguments"]["view"] = contracts.view
    files = [
        CapturedRecordFile(
            f"analysis/{contracts.view}.json" if result else "analysis/error.json",
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
    counts = analysis["report"]["counts"]
    label = "Row-index" if "row_index" in analysis["report"]["schema"] else "Accounting"
    return (
        f"{label} result: {counts['observations']} observations; {counts['scores']} scores. "
        "Planned evaluations and export completeness unknown; counts do not establish approval."
    )


def render_human(report: dict[str, object]) -> str:
    return render_record_response(
        report, analysis_text=_analysis_text(cast(dict[str, Any], report["analysis"]))
    )


def _record_description(view: View) -> tuple[str, str]:
    if view == "row-index":
        return "langfuse.row-index", "Langfuse row-index capture"
    return "langfuse.local-accounting", "Langfuse local accounting capture"


def run_record_langfuse(
    observations: Path,
    scores: Path,
    root: Path,
    identifier: str,
    *,
    as_json: bool = True,
    view: View = "accounting",
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    selected_schema = record_schema(view)
    contracts = _contracts(view)
    paths = {"observations": observations, "scores": scores}
    captured, sources, analysis = _analyze(paths, view)
    envelope: dict[str, Any] = {
        "schema": selected_schema,
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
    metadata = {
        "analysis_state": analysis["state"],
        "selection_mode": AUTO_PROFILE,
        **_applied_reader(analysis),
    }
    if view == "row-index":
        metadata["view"] = view
    kind, label = _record_description(view)
    spec = CapturedRecordSpec(
        record_id=identifier,
        kind=kind,
        label=label,
        metadata=metadata,
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
    return run_record_langfuse(
        cast(Path, args.observations),
        cast(Path, args.scores),
        cast(Path, args.root),
        cast(str, args.id),
        as_json=cast(bool, args.json),
        view=cast(View, args.view),
    )


def main(argv: list[str]) -> int:
    return run_cli(
        COMMAND,
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: record_schema(args.view),
            max_bytes=OUTPUT_MAX_BYTES,
            human=render_human,
            exit_status=lambda report: cast(int, report["exit_code"]),
        ),
    )
