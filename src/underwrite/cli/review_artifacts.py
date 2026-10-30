"""Expose selected Goppi/Promptfoo source-claim alignment through one CLI command."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.ingest.cross_artifact import (
    READINESS_SCHEMA,
    RESULT_SCHEMA,
    CrossArtifactInputError,
    CrossArtifactResultError,
    review_artifact_files,
    review_artifact_readiness_files,
)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Review selected Goppi C55 and Promptfoo source claims. The default strict view "
        "requires owner-authored case IDs and subject/control roles on every Promptfoo row. "
        "Without a case map, --view readiness reports missing metadata or subject rows while "
        "preserving unknown roles. Neither view establishes shared task inputs, metric "
        "equivalence, producer provenance, improvement, or approval."
    )
    parser.add_argument("--goppi-assessment", required=True, type=Path, metavar="FILE")
    parser.add_argument("--goppi-result", required=True, type=Path, metavar="FILE")
    parser.add_argument("--promptfoo", required=True, type=Path, metavar="FILE")
    parser.add_argument("--correspondence", type=Path, metavar="FILE")
    parser.add_argument(
        "--view",
        choices=("review", "readiness"),
        default="review",
        help="Select strict case review or no-map input readiness with unknown roles preserved.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the selected view as JSON.")


def produce(args: argparse.Namespace) -> dict[str, object]:
    if args.view == "readiness" and args.correspondence is not None:
        raise EvidenceError("USAGE_ERROR", "CORRESPONDENCE_NOT_SUPPORTED_FOR_READINESS")
    try:
        if args.view == "readiness":
            return review_artifact_readiness_files(
                cast(Path, args.goppi_assessment),
                cast(Path, args.goppi_result),
                cast(Path, args.promptfoo),
            )
        return review_artifact_files(
            cast(Path, args.goppi_assessment),
            cast(Path, args.goppi_result),
            cast(Path, args.promptfoo),
            cast(Path | None, args.correspondence),
        )
    except (CrossArtifactInputError, CrossArtifactResultError) as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def render_human(report: dict[str, object]) -> str:
    if report["schema"] == READINESS_SCHEMA:
        metadata = cast(dict[str, object], report["metadata"])
        blockers = cast(list[dict[str, object]], metadata["blockers"])
        return (
            f"Cross-artifact input readiness: {report['review_prerequisites']}; "
            f"metadata blockers={len(blockers)}. No case comparison, producer provenance, "
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
            lambda args: READINESS_SCHEMA if args.view == "readiness" else RESULT_SCHEMA,
            human=render_human,
        ),
    )
