"""Expose selected Goppi/Promptfoo source-claim alignment through one CLI command."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.evidence import (
    EvidenceError,
    ReportOutput,
    output_limit_error,
    output_line_size,
    run_cli,
)
from underwrite.cli.local_json import validate
from underwrite.instrument.ingest.cross_artifact import (
    READINESS_SCHEMA,
    RESULT_SCHEMA,
    CrossArtifactInputError,
    CrossArtifactResultError,
    review_artifact_files,
    review_artifact_readiness_files,
)
from underwrite.instrument.ingest.schema import packaged_validator

SUMMARY_SCHEMA = "cross_artifact_review_summary.v1"
READINESS_SUMMARY_SCHEMA = "cross_artifact_readiness_summary.v1"
SUMMARY_OUTPUT_MAX_BYTES = 16 * 1024


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Review selected Goppi C55 and Promptfoo source claims. The default strict view "
        "requires owner-authored case IDs and subject/control roles on every Promptfoo row. "
        "Without a case map, --view readiness reports missing metadata or subject rows while "
        "preserving unknown roles. --view readiness-summary omits blocker coordinates. "
        "--view summary omits per-pair coordinates and source "
        "strings from the strict review. No view establishes shared task inputs, metric "
        "equivalence, producer provenance, improvement, or approval."
    )
    parser.add_argument("--goppi-assessment", required=True, type=Path, metavar="FILE")
    parser.add_argument("--goppi-result", required=True, type=Path, metavar="FILE")
    parser.add_argument("--promptfoo", required=True, type=Path, metavar="FILE")
    parser.add_argument("--correspondence", type=Path, metavar="FILE")
    parser.add_argument(
        "--view",
        choices=("review", "readiness", "summary", "readiness-summary"),
        default="review",
        help="Select strict review or readiness, each with an optional bounded summary.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the selected view as JSON.")


def summarize(review: dict[str, object]) -> dict[str, object]:
    """Retain closed source-claim meanings while omitting individual coordinates."""
    validate(packaged_validator(RESULT_SCHEMA), review, EvidenceError, "INTERNAL_ERROR")
    correspondence = cast(dict[str, Any], review["correspondence"])
    readers = cast(dict[str, Any], review["readers"])
    promptfoo_reader = cast(dict[str, Any], readers["promptfoo"])
    tasks = cast(list[dict[str, Any]], review["next_verification_tasks"])
    task_counts: dict[tuple[str, str], int] = {}
    for task in tasks:
        key = (cast(str, task["code"]), cast(str, task["owner"]))
        task_counts[key] = task_counts.get(key, 0) + 1
    summary: dict[str, object] = {
        "schema": SUMMARY_SCHEMA,
        "view": "summary",
        "readers": {
            "goppi": readers["goppi"],
            "promptfoo": {
                name: promptfoo_reader[name]
                for name in ("profile", "producer_version_state")
            },
        },
        **{
            name: review[name]
            for name in (
                "relation",
                "source_identity",
                "populations",
                "finding_counts",
                "improvement",
                "checks",
                "limitations",
            )
        },
        "correspondence": {
            **{
                name: correspondence[name]
                for name in (
                    "declaration_sha256",
                    "coverage",
                    "input_correspondence",
                    "semantic_equivalence",
                    "condition_alignment",
                )
            },
            "unmatched_case_count": len(correspondence["unmatched_case_indices"]),
            "unmatched_subject_row_count": len(correspondence["unmatched_subject_row_indices"]),
        },
        "next_verification_tasks": [
            {"code": code, "owner": owner, "count": count}
            for (code, owner), count in sorted(task_counts.items())
        ],
        "detail_state": {
            "pair_details": "intentionally_omitted",
            "unmatched_coordinates": "intentionally_omitted",
            "task_coordinates": "intentionally_omitted",
            "producer_version": "not_applicable"
            if promptfoo_reader["producer_version_state"] == "unknown"
            else "intentionally_omitted",
            "full_view": "review-artifacts --view review",
        },
    }
    validate(packaged_validator(SUMMARY_SCHEMA), summary, EvidenceError, "INTERNAL_ERROR")
    if output_line_size(canonical_bytes(summary)) > SUMMARY_OUTPUT_MAX_BYTES:
        raise output_limit_error()
    return summary


def summarize_readiness(readiness: dict[str, object]) -> dict[str, object]:
    """Aggregate missing owner metadata without publishing row coordinates."""
    validate(packaged_validator(READINESS_SCHEMA), readiness, EvidenceError, "INTERNAL_ERROR")
    metadata = cast(dict[str, Any], readiness["metadata"])
    readers = cast(dict[str, Any], readiness["readers"])
    promptfoo_reader = cast(dict[str, Any], readers["promptfoo"])
    blocker_counts: dict[str, int] = {}
    for blocker in cast(list[dict[str, Any]], metadata["blockers"]):
        code = cast(str, blocker["code"])
        blocker_counts[code] = blocker_counts.get(code, 0) + 1
    task_counts: dict[tuple[str, str], int] = {}
    for task in cast(list[dict[str, Any]], readiness["next_verification_tasks"]):
        key = (cast(str, task["code"]), cast(str, task["owner"]))
        task_counts[key] = task_counts.get(key, 0) + 1
    summary: dict[str, object] = {
        "schema": READINESS_SUMMARY_SCHEMA,
        "view": "readiness-summary",
        **{
            name: readiness[name]
            for name in (
                "relation", "source_identity", "populations", "review_prerequisites",
                "finding_counts", "improvement", "checks", "limitations",
            )
        },
        "readers": {
            "goppi": readers["goppi"],
            "promptfoo": {
                name: promptfoo_reader[name]
                for name in ("profile", "producer_version_state")
            },
        },
        "metadata": {
            "case_ids": metadata["case_ids"],
            "row_roles": metadata["row_roles"],
            "blockers": [
                {"code": code, "count": count}
                for code, count in sorted(blocker_counts.items())
            ],
        },
        "next_verification_tasks": [
            {"code": code, "owner": owner, "count": count}
            for (code, owner), count in sorted(task_counts.items())
        ],
        "detail_state": {
            "blocker_coordinates": "intentionally_omitted",
            "task_coordinates": "intentionally_omitted",
            "producer_version": "not_applicable"
            if promptfoo_reader["producer_version_state"] == "unknown"
            else "intentionally_omitted",
            "full_view": "review-artifacts --view readiness",
        },
    }
    validate(packaged_validator(READINESS_SUMMARY_SCHEMA), summary, EvidenceError, "INTERNAL_ERROR")
    if output_line_size(canonical_bytes(summary)) > SUMMARY_OUTPUT_MAX_BYTES:
        raise output_limit_error()
    return summary


def produce(args: argparse.Namespace) -> dict[str, object]:
    if args.view in ("readiness", "readiness-summary") and args.correspondence is not None:
        raise EvidenceError("USAGE_ERROR", "CORRESPONDENCE_NOT_SUPPORTED_FOR_READINESS")
    try:
        if args.view in ("readiness", "readiness-summary"):
            readiness = review_artifact_readiness_files(
                cast(Path, args.goppi_assessment),
                cast(Path, args.goppi_result),
                cast(Path, args.promptfoo),
            )
            return summarize_readiness(readiness) if args.view == "readiness-summary" else readiness
        review = review_artifact_files(
            cast(Path, args.goppi_assessment),
            cast(Path, args.goppi_result),
            cast(Path, args.promptfoo),
            cast(Path | None, args.correspondence),
        )
        return summarize(review) if args.view == "summary" else review
    except (CrossArtifactInputError, CrossArtifactResultError) as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def render_human(report: dict[str, object]) -> str:
    if report["schema"] in (READINESS_SCHEMA, READINESS_SUMMARY_SCHEMA):
        metadata = cast(dict[str, object], report["metadata"])
        blockers = cast(list[dict[str, object]], metadata["blockers"])
        blocker_count = sum(cast(int, item.get("count", 1)) for item in blockers)
        return (
            f"Cross-artifact input readiness: {report['review_prerequisites']}; "
            f"metadata blockers={blocker_count}. No case comparison, producer provenance, "
            "improvement or approval is established."
        )
    relation = report["relation"]
    counts = cast(dict[str, int], report["finding_counts"])
    return (
        f"Source-claim relation: {relation}; declared Boolean fields equal={counts['equal']}, "
        f"different={counts['different']}, ineligible={counts['ineligible']}. "
        "Input equivalence, producer provenance, improvement and approval remain unverified."
    )


def main(argv: list[str]) -> int:
    return run_cli(
        "review-artifacts",
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: READINESS_SCHEMA
            if args.view == "readiness"
            else READINESS_SUMMARY_SCHEMA
            if args.view == "readiness-summary"
            else SUMMARY_SCHEMA
            if args.view == "summary"
            else RESULT_SCHEMA,
            human=render_human,
        ),
    )
