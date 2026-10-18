"""Capture a DeepEval TestRun and owner binding pack before bounded analysis."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.cli.audit_deepeval import AUTO_BOUND_HUMAN_LIMIT
from underwrite.cli.evidence import EvidenceError
from underwrite.cli.local_json import Validator, validate
from underwrite.cli.record_capture import (
    STORAGE_FAILURE_EXIT,
    admit_record_response,
    render_record_response,
    storage_validator,
    store_captured_record,
)
from underwrite.cli.records import OUTPUT_MAX_BYTES, record_error_payload
from underwrite.instrument.evidence.deepeval_binding import (
    AUTO_PACK_SCHEMA,
    AUTO_RESULT_SCHEMA,
    DeepevalBindingError,
)
from underwrite.instrument.evidence.deepeval_compat import ACCOUNTING_SCHEMA
from underwrite.instrument.ingest.deepeval_accounting import (
    SOURCE,
    DeepevalFileError,
    read_deepeval_file,
)
from underwrite.instrument.ingest.deepeval_binding import (
    audit_deepeval_bound_bytes,
    read_pack_bytes,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import (
    RECORD_READ_MAX_BYTES,
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
)

SCHEMA = "deepeval_bound_record.v1"
AUTO_SCHEMA = "deepeval_bound_record.v2"
COMMAND = "records audit-deepeval"
VERSION = "4.2.8"


@dataclass(frozen=True)
class _Contracts:
    aggregate: Validator
    storage: Validator
    result: Validator
    evidence_error: Validator
    record_error: Validator
    verification: Validator


def _contracts(version: str | None) -> _Contracts:
    aggregate = packaged_validator(AUTO_SCHEMA if version is None else SCHEMA)
    storage = storage_validator(aggregate)
    packaged_validator(ACCOUNTING_SCHEMA if version is None else "observation.v1")
    packaged_validator(AUTO_PACK_SCHEMA if version is None else "deepeval_metric_bindings.v1")
    return _Contracts(
        cast(Validator, aggregate),
        storage,
        cast(
            Validator,
            packaged_validator(AUTO_RESULT_SCHEMA if version is None else "evidence_result.v1"),
        ),
        cast(Validator, packaged_validator("evidence_error.v1")),
        cast(Validator, packaged_validator("record_error.v1")),
        cast(Validator, packaged_validator("record_verification.v1")),
    )


def _check(validator: Validator, value: object) -> None:
    validate(validator, value, EvidenceError, "INTERNAL_ERROR")


def _acquire(
    paths: dict[str, Path],
) -> tuple[dict[str, bytes], dict[str, object], dict[str, dict[str, object]]]:
    captured: dict[str, bytes] = {}
    sources: dict[str, object] = {}
    errors: dict[str, dict[str, object]] = {}
    for role, reader in (("bindings", read_pack_bytes), ("deepeval", read_deepeval_file)):
        try:
            raw = reader(paths[role])
        except (DeepevalBindingError, DeepevalFileError) as exc:
            if exc.code == "SCHEMA_CONFIGURATION_ERROR":
                raise EvidenceError(exc.code, exc.reason, exc.location) from None
            error = EvidenceError(exc.code, exc.reason, exc.location).payload
        except Exception:
            error = EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR").payload
        else:
            captured[role] = raw
            sources[role] = {"state": "captured", "raw_hash": digest_bytes(raw), "bytes": len(raw)}
            continue
        sources[role] = {"state": "unavailable", "error": error}
        errors[role] = error
    return captured, sources, errors


def _analyze(
    paths: dict[str, Path],
    version: str | None,
) -> tuple[dict[str, bytes], dict[str, object], dict[str, Any]]:
    captured, sources, errors = _acquire(paths)
    if errors:
        primary = errors.get("bindings") or errors["deepeval"]
        return captured, sources, {"state": "error", "phase": "acquisition", "error": primary}
    try:
        result = audit_deepeval_bound_bytes(
            captured["deepeval"], captured["bindings"], version=version
        )
        source = cast(dict[str, Any], result["source"])
        identity = cast(dict[str, object], source["identity"])
        selected = cast(dict[str, object], sources["deepeval"])
        if identity["raw_hash"] != selected["raw_hash"]:
            raise EvidenceError("INTERNAL_ERROR", "RAW_IDENTITY_MISMATCH")
        if version is None:
            declaration = cast(dict[str, object], result["declaration_source"])
            bindings = cast(dict[str, object], sources["bindings"])
            if declaration["raw_hash"] != bindings["raw_hash"]:
                raise EvidenceError("INTERNAL_ERROR", "RAW_IDENTITY_MISMATCH")
        analysis: dict[str, Any] = {"state": "result", "report": result}
    except DeepevalBindingError as exc:
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
    analysis = cast(dict[str, Any], envelope["analysis"])
    result = analysis["state"] == "result"
    document = analysis["report"] if result else analysis["error"]
    _check(contracts.result if result else contracts.evidence_error, document)
    encoded = canonical_bytes(document) + b"\n"
    if len(encoded) > RECORD_READ_MAX_BYTES:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    arguments = {
        "source_path_display": unicodedata.normalize("NFC", str(paths["deepeval"])),
        "bindings_path_display": unicodedata.normalize("NFC", str(paths["bindings"])),
        "root_display": unicodedata.normalize("NFC", str(root)),
        "record_id": identifier,
    }
    if envelope["schema"] == AUTO_SCHEMA:
        arguments["selection_mode"] = "auto"
        if result:
            arguments["reader_profile"] = document["source"]["reader_profile"]
    else:
        arguments["format_version"] = VERSION
    invocation = {
        "command": COMMAND,
        "execution_mode": "in_process",
        "arguments": arguments,
        "sources": envelope["sources"],
        "analysis_state": analysis["state"],
        "analysis_exit_code": 0 if result else 2,
        "analysis_exit_code_scope": "component_contract_not_os_process",
        "aggregate_outcome_location": "command_output",
    }
    files = [
        CapturedRecordFile(
            "analysis/evidence_result.json" if result else "analysis/error.json",
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
    population = analysis["report"]["population"]
    summary = (
        f"Bound DeepEval result: {population['observed_count']} metric occurrences. "
        "Name meanings are owner asserted; source claims remain unverified. No approval authority."
    )
    return (
        summary + " " + AUTO_BOUND_HUMAN_LIMIT
        if analysis["report"]["schema"] == AUTO_RESULT_SCHEMA
        else summary
    )


def render_human(report: dict[str, object]) -> str:
    return render_record_response(
        report, analysis_text=_analysis_text(cast(dict[str, Any], report["analysis"]))
    )


def run_record_bound_deepeval(
    path: Path,
    bindings: Path,
    root: Path,
    identifier: str,
    *,
    version: str | None = None,
    as_json: bool = True,
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    if version is not None and version != VERSION:
        raise EvidenceError(
            "UNSUPPORTED_FORMAT", "DEEPEVAL_BINDING_VERSION_UNSUPPORTED", "/version"
        )
    contracts = _contracts(version)
    paths = {"bindings": bindings, "deepeval": path}
    captured, sources, analysis = _analyze(paths, version)
    envelope: dict[str, Any] = {
        "schema": AUTO_SCHEMA if version is None else SCHEMA,
        "command": COMMAND,
        "sources": sources,
        "analysis": analysis,
        "exit_code": 0 if analysis["state"] == "result" else 2,
    }
    if version is not None:
        envelope["source_format_version"] = VERSION
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
    metadata = (
        {
            "format": SOURCE[0],
            "format_version": VERSION,
            "analysis_state": analysis["state"],
        }
        if version is not None
        else {
            "format": SOURCE[0],
            "analysis_state": analysis["state"],
            "selection_mode": "auto",
        }
    )
    if version is None and analysis["state"] == "result":
        metadata["reader_profile"] = analysis["report"]["source"]["reader_profile"]
    spec = CapturedRecordSpec(
        record_id=identifier,
        kind="deepeval.bound",
        label="DeepEval bound result capture",
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
