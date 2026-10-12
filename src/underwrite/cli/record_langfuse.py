"""Card: record-reuse | Paper: langfuse_record.v1 | Stage: storage-capture.

In-process accounting and capture of the same two selected byte buffers.
"""

from __future__ import annotations

import argparse
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.cli.evidence import (
    EvidenceError,
    ReportOutput,
    output_line_size,
    run_cli,
)
from underwrite.cli.local_json import Validator, validate
from underwrite.cli.record_capture import saved_record, store_captured_record
from underwrite.cli.records import OUTPUT_MAX_BYTES, record_error_candidates, record_error_payload
from underwrite.instrument.ingest.langfuse_accounting import (
    SOURCES,
    LangfuseFileError,
    audit_langfuse_bytes,
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

SCHEMA = "langfuse_record.v1"
COMMAND = "records audit-langfuse"
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
    aggregate = packaged_validator("langfuse_record.v1")
    schema = cast(dict[str, Any], aggregate.schema)
    storage = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/storage"})
    packaged_validator("observation.v1")
    return _Contracts(
        cast(Validator, aggregate),
        cast(Validator, storage),
        cast(Validator, packaged_validator("langfuse_accounting.v1")),
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
        "--observations", type=Path, required=True, help="Entire observations export to retain."
    )
    parser.add_argument(
        "--scores", type=Path, required=True, help="Entire scores export to retain."
    )
    parser.add_argument("--root", type=Path, required=True, help="Explicit private record root.")
    parser.add_argument("--id", type=_identifier, required=True, help="New immutable record ID.")
    parser.add_argument("--json", action="store_true", help="Emit langfuse_record.v1 JSON.")
    parser.description = (
        "Audit two local Langfuse 4.35.0 exports in process and retain the exact complete input, "
        "result or diagnostic, and effective invocation. Malformed complete input is retained "
        "in full; a diagnostic record consumes the ID. No producer or subprocess is run. "
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


def _analyze(paths: dict[str, Path]) -> tuple[dict[str, bytes], dict[str, object], dict[str, Any]]:
    captured, sources, errors = _acquire(paths)
    if errors:
        return captured, sources, {"state": "error", "phase": "acquisition", "error": errors[0]}
    try:
        report = audit_langfuse_bytes(captured["observations"], captured["scores"])
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
    _check(contracts.accounting if result else contracts.evidence_error, document)
    encoded = canonical_bytes(document) + b"\n"
    if len(encoded) > RECORD_READ_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    invocation = {
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
    files[:0] = [
        CapturedRecordFile(f"source/{role}.bin", "source", raw) for role, raw in captured.items()
    ]
    return tuple(files)


def _analysis_text(analysis: dict[str, Any]) -> str:
    if analysis["state"] == "error":
        error = analysis["error"]
        return f"Analysis failed during {analysis['phase']}: {error['code']} ({error['reason']})."
    counts = analysis["report"]["counts"]
    return (
        f"Accounting result: {counts['observations']} observations; {counts['scores']} scores. "
        "Planned evaluations and export completeness unknown; counts do not establish approval."
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
    representative = envelope.get("storage") or saved_record(identifier, digest, count)
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


def run_record_langfuse(
    observations: Path, scores: Path, root: Path, identifier: str, *, as_json: bool = True
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    contracts = _contracts()
    paths = {"observations": observations, "scores": scores}
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
        envelope["exit_code"] = _STORAGE_FAILURE_EXIT
        _admit_response(envelope, contracts, identifier, 0, as_json)
        return envelope
    _admit_response(envelope, contracts, identifier, len(files), as_json)
    spec = CapturedRecordSpec(
        record_id=identifier,
        kind="langfuse.accounting",
        label="Langfuse accounting capture",
        metadata={
            **{
                f"{role}_{key}": value[index]
                for role, value in SOURCES.items()
                for index, key in enumerate(("format", "format_version"))
            },
            "analysis_state": analysis["state"],
        },
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
        envelope["exit_code"] = _STORAGE_FAILURE_EXIT
    return envelope


def produce(args: argparse.Namespace) -> dict[str, object]:
    return run_record_langfuse(
        cast(Path, args.observations),
        cast(Path, args.scores),
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
