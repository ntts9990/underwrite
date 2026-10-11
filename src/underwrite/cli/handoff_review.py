"""Compose a bounded handoff review and optional immutable capture in one invocation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import canonical_bytes, content_digest

from underwrite.cli.evidence import (
    ERROR_ACTIONS,
    EvidenceError,
    ReportOutput,
    output_line_size,
    run_cli,
)
from underwrite.cli.handoff_limits import (
    GENERATED_NAMES,
    MAX_FILE_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_SOURCE_FILES,
    MAX_TOTAL_BYTES,
    PROFILE,
)
from underwrite.cli.local_json import validate
from underwrite.cli.pair_binary import computation_error
from underwrite.cli.records import record_error_payload
from underwrite.instrument.evidence.goppi_p7_handoff import (
    MAPPING_HANDOFF,
    HandoffMappingError,
    VerifiedHandoff,
    verify_goppi_p7_mapping,
)
from underwrite.instrument.evidence.paired_binary import PairedBinaryError
from underwrite.instrument.evidence.paired_review import PairedReviewError, evaluate_paired_review
from underwrite.instrument.ingest.goppi_p7_handoff import (
    HandoffCapture,
    HandoffInputError,
    capture_goppi_p7_handoff,
)
from underwrite.instrument.ingest.local_capture import CaptureError, CapturePolicy
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service.records import (
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
    save_captured_record,
    verify_record,
)


@dataclass(frozen=True)
class HandoffRequest:
    handoff_dir: Path
    source_root: Path | None = None
    record_root: Path | None = None
    record_id: str | None = None
    arguments: tuple[str, ...] | None = None


@dataclass(frozen=True)
class HandoffRun:
    envelope: dict[str, object]
    exit_code: int


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Review one exact local goppi P7 handoff and optionally save its evidence. "
        "No producer execution or independent authentication."
    )
    parser.add_argument("--handoff", required=True, type=Path, help="Handoff bundle directory.")
    parser.add_argument("--source-root", type=Path, help="Explicit root for all external pointers.")
    parser.add_argument("--record-root", type=Path, help="Optional private immutable record store.")
    parser.add_argument("--record-id", help="New record ID; requires --record-root.")
    parser.add_argument("--json", action="store_true", help="Emit paired_handoff_review.v1.")


def _validate(name: str, document: object) -> None:
    validate(packaged_validator(name), document, EvidenceError, "INTERNAL_ERROR")


def _diagnostic(exc: Exception) -> dict[str, object]:
    if isinstance(exc, EvidenceError):
        return exc.payload
    if isinstance(exc, PairedBinaryError):
        return computation_error(exc).payload
    if isinstance(exc, PairedReviewError):
        return EvidenceError(exc.code, exc.reason, "/context").payload
    if isinstance(exc, (HandoffInputError, HandoffMappingError, CaptureError)):
        code = exc.code if exc.code in ERROR_ACTIONS else "INVALID_INPUT"
        return EvidenceError(code, exc.reason, exc.location).payload
    if isinstance(exc, SchemaConfigurationError):
        return EvidenceError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT").payload
    return EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR").payload


def _summary(
    verified: VerifiedHandoff, evidence: dict[str, object], review: dict[str, object]
) -> dict[str, object]:
    rows = cast(list[dict[str, object]], verified.input_doc["slot_records"])
    counts: dict[str, object] = {}
    for arm in ("baseline", "candidate"):
        counts[arm] = {
            "observed_one": sum(row.get("outcome") == 1 for row in rows if row["arm"] == arm),
            "observed_zero": sum(row.get("outcome") == 0 for row in rows if row["arm"] == arm),
        }
    return {
        **counts,
        "transitions": cast(dict[str, object], evidence["accounting"])["primary_transitions"],
        "measurement_status": evidence["status"],
        "measurement_reason": evidence["reason"],
        "conditions_coverage": cast(dict[str, object], review["conditions"])["coverage"],
    }


def _analyze(capture: HandoffCapture) -> tuple[VerifiedHandoff, dict[str, object]]:
    # Installed contracts remain authoritative for the supplied underwrite input/policy.
    for name, value in (
        ("paired_binary_input.v1", capture.documents.paired_input),
        ("paired_binary_policy.v1", capture.documents.analysis_policy),
    ):
        validate(packaged_validator(name), value, EvidenceError, "INVALID_INPUT", "/handoff")
    verified = verify_goppi_p7_mapping(capture.documents)
    _validate("paired_review_context.v1", verified.context_doc)
    evidence, review = evaluate_paired_review(
        verified.input_doc, verified.policy_doc, verified.context_doc
    )
    _validate("paired_binary_evidence.v1", evidence)
    _validate("paired_review.v1", review)
    expected = cast(dict[str, object], verified.context_doc["binding"])
    actual = cast(dict[str, object], review["paired_evidence_ref"])
    if (
        evidence["input_digest"] != expected["input_digest"]
        or evidence["policy_digest"] != expected["policy_digest"]
        or actual["digest"] != content_digest(evidence)
    ):
        raise EvidenceError("INTERNAL_ERROR", "DIGEST_DISAGREEMENT")
    return verified, {
        "state": "result",
        "paired_evidence": evidence,
        "paired_review": review,
        "summary": _summary(verified, evidence, review),
    }


def _next_actions(analysis: dict[str, Any]) -> list[str]:
    if analysis["state"] == "error":
        return [analysis["error"]["next_action"]]
    actions: list[str] = []
    if analysis["summary"]["measurement_reason"] == "ASSUMPTIONS_NOT_DECLARED":
        actions.append("DECLARE_JUSTIFIED_ANALYSIS_ASSUMPTIONS_OR_KEEP_DESCRIPTIVE")
    if analysis["summary"]["conditions_coverage"] == "unknown":
        actions.append("SUPPLY_PRODUCER_REVIEWED_COMPARISON_CONDITIONS")
    return actions


def _generated(
    envelope: dict[str, Any], verified: VerifiedHandoff | None, request: HandoffRequest
) -> tuple[CapturedRecordFile, ...]:
    analysis = envelope["analysis"]
    # This records an analysis phase, not an invented exit from the enclosing save operation.
    documents: dict[str, tuple[str, object]] = {
        "underwrite/invocation.json": (
            "log",
            {
                "entry_point": "underwrite review-handoff",
                "arguments": list(request.arguments) if request.arguments is not None else None,
                "analysis_exit_code": 0 if analysis["state"] == "result" else 2,
                "scope": "analysis_phase",
                "storage_outcome_location": "outer_cli_envelope",
            },
        ),
    }
    if verified is not None and analysis["state"] == "result":
        documents.update(
            {
                "underwrite/context.json": ("input", verified.context_doc),
                "underwrite/evidence.json": ("result", analysis["paired_evidence"]),
                "underwrite/review.json": ("result", analysis["paired_review"]),
                "underwrite/summary.json": ("result", analysis["summary"]),
                "underwrite/findings.json": (
                    "verification",
                    {
                        key: envelope[key]
                        for key in ("scope", "source", "findings", "context_provenance")
                    },
                ),
            }
        )
    else:
        documents["underwrite/error.json"] = ("log", analysis)
    if len(documents) > len(GENERATED_NAMES):
        raise RecordError("INPUT_LIMIT_EXCEEDED")
    return tuple(
        CapturedRecordFile(name, role, canonical_bytes(document) + b"\n", "application/json")
        for name, (role, document) in documents.items()
    )


def _store(
    request: HandoffRequest,
    capture: HandoffCapture | None,
    verified: VerifiedHandoff | None,
    envelope: dict[str, Any],
) -> dict[str, object]:
    if request.record_root is None or request.record_id is None:
        return {"state": "not_requested"}
    published: dict[str, object] | None = None
    try:
        files = (
            tuple(
                CapturedRecordFile(item.logical_name, "source", item.data)
                for item in capture.artifacts
            )
            if capture is not None
            else ()
        )
        files += _generated(envelope, verified, request)
        spec = CapturedRecordSpec(
            record_id=request.record_id,
            kind="handoff.review",
            label="Local paired handoff review",
            metadata={"profile": PROFILE, "analysis_state": envelope["analysis"]["state"]},
            links=(),
            selection_excluded=(
                "Credential keys and private authentication material",
                "Raw model answers, runner streams, and private evaluation material bodies",
                "Files outside the exact declared handoff profile",
                "Consumer executable and dependency environment",
            ),
            rerun_verification={"status": "not_performed", "reference": None},
            provenance={"verified": False, "attestation_ref": None},
            files=files,
        )
        snapshot = save_captured_record(request.record_root, spec)
        published = {"record_id": request.record_id, "manifest_digest": snapshot.digest}
        verification = verify_record(request.record_root, request.record_id)
        _validate("record_verification.v1", verification)
        if verification["valid"] is not True or verification["manifest_digest"] != snapshot.digest:
            raise RecordError("STORE_IO_ERROR")
        return {"state": "saved", **published, "verification": verification}
    except RecordError as exc:
        error = record_error_payload(exc)
    except Exception:
        error = record_error_payload(RecordError("INTERNAL_ERROR"))
    # A post-publication fsync error remains error. Immediate readback cannot prove durability.
    _validate("record_error.v1", error)
    return {"state": "error", "error": error, "published_record": published}


def _empty_envelope() -> dict[str, Any]:
    return {
        "schema": "paired_handoff_review.v1",
        "profile": PROFILE,
        "exit_code": 2,
        "scope": None,
        "source": None,
        "context_provenance": None,
        "findings": [],
        "analysis": {},
        "storage": {"state": "not_requested"},
        "next_actions": [],
    }


def run_goppi_p7_handoff(request: HandoffRequest) -> HandoffRun:
    """Review immutable captured bytes; only an explicit store request creates records."""
    if (request.record_root is None) != (request.record_id is None):
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP")
    capture: HandoffCapture | None = None
    verified: VerifiedHandoff | None = None
    envelope = _empty_envelope()
    phase = "acquisition"
    try:
        capture = capture_goppi_p7_handoff(
            request.handoff_dir,
            source_root=request.source_root,
            policy=CapturePolicy(MAX_SOURCE_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES),
        )
        phase = "analysis"
        envelope["source"] = {
            "handoff_digest": capture.artifact_by_name[MAPPING_HANDOFF].sha256,
            "files": [
                {"name": item.logical_name, "sha256": item.sha256, "bytes": item.size}
                for item in capture.artifacts
            ],
            "authentication_independently_verified": False,
        }
        verified, analysis = _analyze(capture)
        envelope.update(
            scope=verified.scope,
            context_provenance=verified.context_provenance,
            findings=[{"code": f.code, "source": f.source} for f in verified.findings],
            analysis=analysis,
        )
    except Exception as exc:
        diagnostic = _diagnostic(exc)
        _validate("evidence_error.v1", diagnostic)
        envelope["analysis"] = {"state": "error", "phase": phase, "error": diagnostic}
    envelope["exit_code"] = 0 if envelope["analysis"]["state"] == "result" else 2
    storage_reserve = 2048 if request.record_root is not None else 0
    try:
        _validate("paired_handoff_review.v1", envelope)
        envelope["next_actions"] = _next_actions(envelope["analysis"])
        # Allow room for the bounded storage identity/diagnostic before any store mutation.
        if output_line_size(canonical_bytes(envelope)) > MAX_OUTPUT_BYTES - storage_reserve:
            raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    except SchemaConfigurationError:
        # A broken installed response contract cannot certify an aggregate response.
        raise
    except Exception as exc:
        capture, verified = None, None
        envelope = _empty_envelope()
        envelope["analysis"] = {"state": "error", "phase": "analysis", "error": _diagnostic(exc)}
        envelope["next_actions"] = _next_actions(envelope["analysis"])
        _validate("paired_handoff_review.v1", envelope)
    if output_line_size(canonical_bytes(envelope)) + storage_reserve > MAX_OUTPUT_BYTES:
        # A configured limit smaller than the truthful diagnostic cannot admit a store mutation.
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    envelope["storage"] = _store(request, capture, verified, envelope)
    if envelope["storage"]["state"] == "error":
        envelope["exit_code"] = 3
        action = cast(dict[str, Any], envelope["storage"])["error"]["next_action"]
        if action not in envelope["next_actions"]:
            envelope["next_actions"].append(action)
    _validate("paired_handoff_review.v1", envelope)
    return HandoffRun(envelope, cast(int, envelope["exit_code"]))


def render_human(report: dict[str, object]) -> str:
    analysis = cast(dict[str, Any], report["analysis"])
    storage = cast(dict[str, Any], report["storage"])
    lines: list[str] = []
    if analysis["state"] == "result":
        summary = analysis["summary"]
        scope = cast(dict[str, Any], report["scope"])
        population = analysis["paired_evidence"]["accounting"]["primary_repeat_zero"]
        missing = sum(population["missing_by_reason"].values())
        reason = summary["measurement_reason"]
        lines.extend(
            [
                f"Scope: {scope['stage']}/{scope['split']}; {scope['cases']} paired cases; "
                f"repeat {scope['repeat']}.",
                f"Observed outcome 1: baseline {summary['baseline']['observed_one']}; "
                f"candidate {summary['candidate']['observed_one']}. "
                f"Outcome 0: baseline {summary['baseline']['observed_zero']}; "
                f"candidate {summary['candidate']['observed_zero']}.",
                f"Slots: planned {population['planned']}; reported {population['reported']}; "
                f"missing {missing}; unreported {population['unreported_planned']}.",
                f"Measurement: {summary['measurement_status']}"
                + (f" ({reason})." if reason else "."),
                f"Comparison conditions: {summary['conditions_coverage']}.",
                "Byte identity and source claims do not establish provenance or approval.",
            ]
        )
    else:
        lines.append(f"Analysis failed ({analysis['phase']}): {analysis['error']['code']}.")
    if storage["state"] == "saved":
        lines.append(f"Record: {storage['record_id']} ({storage['manifest_digest']}).")
    elif storage["state"] == "error":
        lines.append(f"Storage failed: {storage['error']['code']}; analysis remains above.")
    for action in cast(list[str], report["next_actions"]):
        lines.append("Next: " + action)
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    def produce(args: argparse.Namespace) -> dict[str, object]:
        return run_goppi_p7_handoff(
            HandoffRequest(
                cast(Path, args.handoff),
                cast(Path | None, args.source_root),
                cast(Path | None, args.record_root),
                cast(str | None, args.record_id),
                tuple(argv),
            )
        ).envelope

    return run_cli(
        "review-handoff",
        argv,
        configure,
        produce,
        ReportOutput(
            "paired_handoff_review.v1",
            MAX_OUTPUT_BYTES,
            exit_status=lambda report: cast(int, report["exit_code"]),
            human=render_human,
        ),
    )
