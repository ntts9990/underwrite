"""One-file DeepEval metric accounting with explicit source-claim limits."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.ingest.deepeval_accounting import (
    PROFILES,
    SOURCE,
    DeepevalFileError,
    audit_deepeval_file,
    profile_for,
)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "file",
        type=Path,
        metavar="FILE",
        help="Local DeepEval TestRun JSON for the selected version.",
    )
    parser.add_argument(
        "--version",
        choices=tuple(PROFILES),
        default=SOURCE[1],
        help="Explicit source version; no detection or fallback.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the selected deepeval_accounting JSON contract."
    )
    parser.description = (
        "Account for case flags, observed metric scores, evaluator errors, and unscored metrics "
        "in the selected DeepEval 4.1.1 or 4.2.8 file. "
        "No manifest or evaluator execution is needed. "
        "A report exits 0 even when source counts disagree or metrics contain errors. "
        "The profile is explicitly selected, not detected from the file. "
        "If missing-execution accounting is required, the inventory next action asks for "
        "external planned evidence from the evaluation owner. This command has no inventory "
        "input; planned and missing-execution counts remain unknown."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        return audit_deepeval_file(cast(Path, args.file), version=cast(str, args.version))
    except DeepevalFileError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def render_human(report: dict[str, object]) -> str:
    counts = cast(dict[str, Any], report["counts"])
    cases, metrics = counts["cases"], counts["metrics"]
    comparison = cast(dict[str, Any], report["declared_case_counts"])["comparison"]
    lines = [
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
        "SUPPLY_PLANNED_INVENTORY_FOR_MISSINGNESS applies only when missing-execution "
        "accounting is needed: obtain explicit planned evidence from the evaluation owner. "
        "This command cannot ingest it; planned and missing-execution counts remain unknown. "
        "Descriptive accounting needs no additional collection.",
    ]
    if report["schema"] == profile_for("4.2.8").accounting_schema:
        lines.extend(
            [
                "Scored metrics with absent source verdict: "
                f"{metrics['scored_source_success_absent']}; "
                "absent verdicts and thresholds remain unknown, not false or zero.",
                f"Source flaky declarations: {cases['source_flaky_true']} cases; "
                f"{metrics['source_flaky_true']} metrics. "
                "These declarations do not establish CI outcomes or measured instability.",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-deepeval",
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: profile_for(cast(str, args.version)).accounting_schema, human=render_human
        ),
    )
