"""Card: paired-evidence | Paper: declared-comparison | Stage: evidence-pilot.

Bind declared review context to one exact paired input and computation policy.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import canonical_bytes

from underwrite.cli import pair_binary
from underwrite.cli.evidence import (
    REPORT_MAX_BYTES,
    EvidenceError,
    ReportOutput,
    output_limit_error,
    output_line_size,
    read_validated,
    run_cli,
)
from underwrite.cli.local_json import validate
from underwrite.instrument.evidence.paired_binary import PairedBinaryError
from underwrite.instrument.evidence.paired_review import (
    MAX_CONTEXT_BYTES,
    PairedReviewError,
    evaluate_paired_review,
)
from underwrite.instrument.ingest.schema import packaged_validator

SCHEMA = "paired_review.v1"
SUMMARY_SCHEMA = "paired_review_summary.v1"
SUMMARY_OUTPUT_MAX_BYTES = 16 * 1024
_CLASSIFICATIONS = (
    "intended_change",
    "unchanged_treatment",
    "invariant_changed",
    "invariant_unchanged",
    "unknown",
)


def configure(parser: argparse.ArgumentParser) -> None:
    pair_binary.configure(parser)
    parser.description = (
        "Review declared changes and invariants bound to a paired input and policy. "
        "The output references the independently computed paired evidence; "
        "declarations do not verify actual conditions or authorize a change."
    )
    parser.add_argument("--context", required=True, help="Local paired_review_context.v1 JSON.")
    parser.add_argument(
        "--view",
        choices=("full", "summary"),
        default="full",
        help="Select the complete review or a bounded summary without declared strings.",
    )


def summarize(review: dict[str, object]) -> dict[str, object]:
    """Project only closed review meanings and counts after full contract validation."""
    validate(packaged_validator(SCHEMA), review, EvidenceError, "INTERNAL_ERROR")
    conditions = cast(dict[str, Any], review["conditions"])
    entries = cast(list[dict[str, Any]], conditions["entries"])
    history = cast(dict[str, Any] | None, review.get("evaluation_history"))
    partition = cast(dict[str, Any] | None, review.get("case_partition"))
    groups = cast(list[dict[str, Any]], partition["groups"]) if partition is not None else []
    summary: dict[str, object] = {
        "schema": SUMMARY_SCHEMA,
        "view": "summary",
        "paired_evidence_ref": review["paired_evidence_ref"],
        "context_digest": review["context_digest"],
        "declared_conditions": {
            "coverage": conditions["coverage"],
            "total": len(entries),
            "classifications": {
                name: sum(entry["classification"] == name for entry in entries)
                for name in _CLASSIFICATIONS
            },
        },
        "history": {
            "state": "present" if history is not None else "absent",
            "record_count": len(cast(list[object], history["records"]))
            if history is not None
            else None,
            "selector_coverage": history["selector_coverage"] if history is not None else None,
        },
        "partition": {
            "state": "present" if partition is not None else "absent",
            "group_count": len(groups) if partition is not None else None,
            "case_count": sum(cast(int, group["case_count"]) for group in groups)
            if partition is not None
            else None,
        },
        "declarations_verified": review["declarations_verified"],
        "actual_conditions_verified": review["actual_conditions_verified"],
        "semantic_equivalence_verified": review["semantic_equivalence_verified"],
        "detail_state": {
            "condition_values": "intentionally_omitted",
            "history_records": "intentionally_omitted" if history is not None else "not_applicable",
            "partition_groups": "intentionally_omitted"
            if partition is not None
            else "not_applicable",
            "full_view": "review-pair --view full",
        },
        "limitations": review["limitations"],
    }
    validate(packaged_validator(SUMMARY_SCHEMA), summary, EvidenceError, "INTERNAL_ERROR")
    if output_line_size(canonical_bytes(summary)) > SUMMARY_OUTPUT_MAX_BYTES:
        raise output_limit_error()
    return summary


def produce(args: argparse.Namespace) -> dict[str, object]:
    input_doc, policy_doc, input_digest, policy_digest = pair_binary.read_inputs(args)
    context, _ = read_validated(
        Path(cast(str, args.context)), "paired_review_context.v1", "/context", MAX_CONTEXT_BYTES, 16
    )
    try:
        evidence, review = evaluate_paired_review(input_doc, policy_doc, context)
    except PairedReviewError as exc:
        raise EvidenceError(exc.code, exc.reason, "/context") from exc
    except PairedBinaryError as exc:
        raise pair_binary.computation_error(exc) from exc
    if evidence["input_digest"] != input_digest or evidence["policy_digest"] != policy_digest:
        raise EvidenceError("INTERNAL_ERROR", "DIGEST_DISAGREEMENT")
    validate(
        packaged_validator("paired_binary_evidence.v1"),
        evidence,
        EvidenceError,
        "INTERNAL_ERROR",
    )
    return summarize(review) if args.view == "summary" else review


def main(argv: list[str]) -> int:
    return run_cli(
        "review-pair",
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: SUMMARY_SCHEMA if args.view == "summary" else SCHEMA,
            REPORT_MAX_BYTES,
        ),
    )
