"""Retain one strict, owner-mapped C55 and Promptfoo review over captured bytes."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.cli.local_json import Validator, validate
from underwrite.cli.record_capture import (
    finalize_captured_record,
    render_record_response,
    storage_validator,
)
from underwrite.instrument.ingest.cross_artifact import (
    RESULT_SCHEMA,
    CapturedMappedReview,
    CrossArtifactInputError,
    CrossArtifactResultError,
    capture_mapped_review_inputs,
    review_captured_mapped_artifacts,
)
from underwrite.instrument.ingest.schema import packaged_validator
from underwrite.service._record_format import FormatError, record_id
from underwrite.service.records import CapturedRecordFile, CapturedRecordSpec

SCHEMA = "cross_artifact_record.v1"
COMMAND = "records review-artifacts"
OUTPUT_MAX_BYTES = 2 * 1024 * 1024
ANALYSIS_MAX_BYTES = 1024 * 1024
INVOCATION_MAX_BYTES = 2048
SOURCE_NAMES = (
    "goppi-assessment.json",
    "goppi-result.json",
    "promptfoo.json",
    "correspondence.json",
)


def _identifier(value: str) -> str:
    try:
        return record_id(value)
    except FormatError:
        raise argparse.ArgumentTypeError("valid new record ID required") from None


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Capture and retain a strict owner-mapped C55 and Promptfoo review. "
        "A complete captured analysis refusal may be retained. Stored bytes do not "
        "authenticate source claims or establish improvement or approval."
    )
    parser.add_argument("--goppi-assessment", type=Path, required=True, metavar="FILE")
    parser.add_argument("--goppi-result", type=Path, required=True, metavar="FILE")
    parser.add_argument("--promptfoo", type=Path, required=True, metavar="FILE")
    parser.add_argument("--correspondence", type=Path, required=True, metavar="FILE")
    parser.add_argument("--root", type=Path, required=True, metavar="ROOT")
    parser.add_argument("--id", type=_identifier, required=True, metavar="NEW_ID")
    parser.add_argument("--json", action="store_true", help=f"Emit {SCHEMA} JSON.")


def _input_error(exc: CrossArtifactInputError) -> EvidenceError:
    return EvidenceError(exc.code, exc.reason, exc.location)


def _raws(captured: CapturedMappedReview) -> tuple[bytes, bytes, bytes, bytes]:
    return (
        captured.sources.assessment_raw,
        captured.sources.result_raw,
        captured.sources.promptfoo_raw,
        captured.declaration_raw,
    )


def _bounded_json(document: object, maximum: int) -> bytes:
    try:
        raw = canonical_bytes(document) + b"\n"
    except CanonicalizationError:
        raise EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR") from None
    if len(raw) > maximum:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    return raw


def _analysis(captured: CapturedMappedReview, result_validator: Validator) -> dict[str, Any]:
    try:
        report = review_captured_mapped_artifacts(captured)
    except CrossArtifactResultError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None
    except CrossArtifactInputError as exc:
        error = _input_error(exc).payload
        validate(packaged_validator("evidence_error.v1"), error, EvidenceError, "INTERNAL_ERROR")
        return {"state": "error", "error": error}
    validate(result_validator, report, EvidenceError, "INTERNAL_ERROR")
    identity = cast(dict[str, Any], report["source_identity"])
    hashes = captured.sources.hashes
    declaration_hash = digest_bytes(captured.declaration_raw)
    relation = cast(dict[str, Any], report["correspondence"])
    if (
        identity != hashes
        or relation["declaration_sha256"] != declaration_hash
        or report["relation"] != "declared_case_mapping_descriptive"
        or cast(dict[str, Any], report["checks"])["declaration_source_hashes"] != "matched"
    ):
        raise EvidenceError(
            "INPUT_BINDING_MISMATCH", "CROSS_ARTIFACT_CAPTURE_IDENTITY_MISMATCH", "/analysis"
        )
    return {"state": "result", "report": report}


def _analysis_text(analysis: dict[str, Any]) -> str:
    if analysis["state"] == "error":
        error = analysis["error"]
        return f"Cross-artifact analysis failed: {error['code']} ({error['reason']})."
    return "Descriptive cross-artifact review retained from captured source claims."


def render_human(report: dict[str, object]) -> str:
    return render_record_response(
        report, analysis_text=_analysis_text(cast(dict[str, Any], report["analysis"]))
    )


def run_record_review(  # noqa: PLR0913 - four explicit source paths and record identity
    assessment: Path,
    result: Path,
    promptfoo: Path,
    correspondence: Path,
    root: Path,
    identifier: str,
    *,
    as_json: bool = True,
) -> dict[str, object]:
    try:
        record_id(identifier)
    except FormatError:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP") from None
    aggregate = packaged_validator(SCHEMA)
    result_validator = packaged_validator(RESULT_SCHEMA)
    verification_validator = packaged_validator("record_verification.v1")
    record_error_validator = packaged_validator("record_error.v1")
    try:
        captured = capture_mapped_review_inputs(assessment, result, promptfoo, correspondence)
    except CrossArtifactInputError as exc:
        raise _input_error(exc) from None
    raws = _raws(captured)
    hashes = tuple(digest_bytes(raw) for raw in raws)
    if captured.sources.hashes != {
        "goppi_assessment_sha256": hashes[0],
        "goppi_result_sha256": hashes[1],
        "promptfoo_export_sha256": hashes[2],
    }:
        raise EvidenceError(
            "INPUT_BINDING_MISMATCH", "CROSS_ARTIFACT_CAPTURE_IDENTITY_MISMATCH", "/source"
        )
    analysis = _analysis(captured, result_validator)
    source = {
        "state": "captured",
        "members": [
            {"name": name, "raw_hash": digest, "bytes": len(raw)}
            for name, raw, digest in zip(SOURCE_NAMES, raws, hashes, strict=True)
        ],
    }
    document = analysis["report"] if analysis["state"] == "result" else analysis["error"]
    analysis_name = (
        "analysis/review.json" if analysis["state"] == "result" else "analysis/error.json"
    )
    files = (
        *(
            CapturedRecordFile("source/" + name, "source", raw)
            for name, raw in zip(SOURCE_NAMES, raws, strict=True)
        ),
        CapturedRecordFile(
            analysis_name,
            "result" if analysis["state"] == "result" else "log",
            _bounded_json(document, ANALYSIS_MAX_BYTES),
            "application/json",
        ),
        CapturedRecordFile(
            "invocation.json",
            "log",
            _bounded_json(
                {
                    "command": COMMAND,
                    "analysis_state": analysis["state"],
                    "analysis_exit_code": 0 if analysis["state"] == "result" else 2,
                    "execution_mode": "in_process",
                },
                INVOCATION_MAX_BYTES,
            ),
            "application/json",
        ),
    )
    envelope: dict[str, Any] = {
        "schema": SCHEMA,
        "command": COMMAND,
        "source": source,
        "analysis": analysis,
        "exit_code": 0 if analysis["state"] == "result" else 2,
    }
    spec = CapturedRecordSpec(
        record_id=identifier,
        kind="cross-artifact.mapped-review",
        label="Cross-artifact mapped review",
        metadata={"analysis_state": analysis["state"]},
        links=(),
        selection_excluded=None,
        rerun_verification={"status": "not_performed", "reference": None},
        provenance={"verified": False, "attestation_ref": None},
        files=files,
    )
    return finalize_captured_record(
        envelope,
        root,
        spec,
        aggregate_validator=aggregate,
        storage_validator=storage_validator(aggregate),
        verification_validator=verification_validator,
        record_error_validator=record_error_validator,
        analysis_text=_analysis_text(analysis),
        as_json=as_json,
        max_bytes=OUTPUT_MAX_BYTES,
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    return run_record_review(
        cast(Path, args.goppi_assessment),
        cast(Path, args.goppi_result),
        cast(Path, args.promptfoo),
        cast(Path, args.correspondence),
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
