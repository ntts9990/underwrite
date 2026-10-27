"""Expose selected Goppi/Promptfoo source-claim alignment through one CLI command."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.ingest.cross_artifact import (
    RESULT_SCHEMA,
    CrossArtifactInputError,
    review_artifact_files,
)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Review selected Goppi C55 and Promptfoo source claims. Without an owner-declared "
        "case map, report incomparable evidence and the next required input. A mapped result "
        "does not establish shared task inputs, metric equivalence, producer provenance, "
        "or improvement."
    )
    parser.add_argument("--goppi-assessment", required=True, type=Path, metavar="FILE")
    parser.add_argument("--goppi-result", required=True, type=Path, metavar="FILE")
    parser.add_argument("--promptfoo", required=True, type=Path, metavar="FILE")
    parser.add_argument("--correspondence", type=Path, metavar="FILE")
    parser.add_argument("--json", action="store_true", help="Emit cross_artifact_review.v1 JSON.")


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        return review_artifact_files(
            cast(Path, args.goppi_assessment),
            cast(Path, args.goppi_result),
            cast(Path, args.promptfoo),
            cast(Path | None, args.correspondence),
        )
    except CrossArtifactInputError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def render_human(report: dict[str, object]) -> str:
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
        ReportOutput(RESULT_SCHEMA, human=render_human),
    )
