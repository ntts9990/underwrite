"""One-file DeepEval metric accounting with explicit source-claim limits."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.ingest.deepeval_accounting import (
    DeepevalFileError,
    audit_deepeval_file,
)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "file", type=Path, metavar="FILE", help="Local DeepEval 4.1.1 TestRun JSON."
    )
    parser.add_argument("--json", action="store_true", help="Emit deepeval_accounting.v1 JSON.")
    parser.description = (
        "Account for case flags, observed metric scores, evaluator errors, and unscored metrics "
        "in a DeepEval 4.1.1 file. No manifest or evaluator execution is needed. "
        "A report exits 0 even when source counts disagree or metrics contain errors. "
        "The profile is explicitly selected, not detected from the file."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        return audit_deepeval_file(cast(Path, args.file))
    except DeepevalFileError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def render_human(report: dict[str, object]) -> str:
    counts = cast(dict[str, Any], report["counts"])
    cases, metrics = counts["cases"], counts["metrics"]
    comparison = cast(dict[str, Any], report["declared_case_counts"])["comparison"]
    return "\n".join(
        [
            f"Cases: {cases['total']}; source true {cases['source_true']}; "
            f"source false {cases['source_false']}; flag absent {cases['source_success_absent']}.",
            f"Metric occurrences: {metrics['total']}; scored {metrics['scored']}; "
            f"evaluator errors {metrics['error']}; unscored {metrics['unscored']}.",
            f"Observed numeric zeros: {metrics['observed_zero']}; "
            "numeric zero is not an inferred task failure.",
            f"Declared case counts: passed {comparison['passed']}; failed {comparison['failed']}.",
            "Planned evaluations unknown; metric aggregates not checked; source claims unverified.",
            "No causal failure attribution, statistical effect, or approval authority.",
            "Next: " + "; ".join(cast(list[str], report["next_actions"])),
        ]
    )


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-deepeval",
        argv,
        configure,
        produce,
        ReportOutput("deepeval_accounting.v1", human=render_human),
    )
