"""One-file DeepEval metric accounting with explicit source-claim limits."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.evidence.deepeval_binding import (
    AUTO_RESULT_SCHEMA,
    DeepevalBindingError,
)
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_SCHEMA as AUTO_ACCOUNTING_SCHEMA,
)
from underwrite.instrument.evidence.deepeval_compat import TRACE_SCHEMA as AUTO_TRACE_SCHEMA
from underwrite.instrument.evidence.deepeval_trace import SCHEMA as TRACE_SCHEMA
from underwrite.instrument.evidence.deepeval_trace import SOURCE_VERSION as TRACE_VERSION
from underwrite.instrument.ingest.deepeval_accounting import (
    PROFILES,
    DeepevalFileError,
    audit_deepeval_file,
    profile_for,
)
from underwrite.instrument.ingest.deepeval_binding import audit_deepeval_bound_file
from underwrite.instrument.ingest.deepeval_trace import audit_deepeval_trace_file

AUTO_ORDINARY_HUMAN_LIMIT = (
    "Only supplied serialized metricsData rows are counted; expectation population is unknown."
)
AUTO_BOUND_HUMAN_LIMIT = (
    AUTO_ORDINARY_HUMAN_LIMIT
    + " Producer version unknown; pack hash identifies only captured declaration bytes."
)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "file",
        type=Path,
        metavar="FILE",
        help="Local DeepEval TestRun JSON.",
    )
    parser.add_argument(
        "--version",
        choices=tuple(PROFILES),
        default=None,
        help="Optional historical source version; omitted selects local automatic rules.",
    )
    parser.add_argument(
        "--bindings",
        type=Path,
        default=None,
        help=(
            "Owner-asserted metric pack: format-only v2 without --version; "
            "v1 with --version 4.2.8."
        ),
    )
    parser.add_argument(
        "--view",
        choices=("accounting", "trace-index"),
        default="accounting",
        help=("Trace-index selects bounded native step statuses; default is accounting."),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the selected accounting, binding, or trace-index JSON contract.",
    )
    parser.description = (
        "Account for case flags, observed metric scores, evaluator errors, and unscored metrics "
        "in the supplied DeepEval TestRun. "
        "The trace-index view reports source-claimed step statuses and local "
        "parent links; instrumentation coverage and causality remain unknown. "
        "No manifest or evaluator execution is needed. "
        "An accounting report exits 0 even when source counts disagree or metrics contain errors. "
        "Without --version, a selected local reader does not establish a producer release. "
        "If missing-execution accounting is required, the inventory next action asks for "
        "external planned evidence from the evaluation owner. This command has no inventory "
        "input; planned and missing-execution counts remain unknown."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        if args.view == "trace-index":
            if args.version is not None and args.version != TRACE_VERSION:
                raise EvidenceError(
                    "UNSUPPORTED_FORMAT", "DEEPEVAL_TRACE_VERSION_UNSUPPORTED", "/version"
                )
            if args.bindings is not None:
                raise EvidenceError(
                    "UNSUPPORTED_FORMAT", "DEEPEVAL_TRACE_BINDINGS_UNSUPPORTED", "/bindings"
                )
            return audit_deepeval_trace_file(cast(Path, args.file), version=args.version)
        if args.bindings is not None:
            if args.version is not None and args.version != "4.2.8":
                raise EvidenceError(
                    "UNSUPPORTED_FORMAT", "DEEPEVAL_BINDING_EXPLICIT_VERSION_REQUIRED", "/version"
                )
            return audit_deepeval_bound_file(
                cast(Path, args.file), cast(Path, args.bindings), version=args.version
            )
        return audit_deepeval_file(cast(Path, args.file), version=args.version)
    except (DeepevalFileError, DeepevalBindingError) as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def render_bound_human(report: dict[str, object]) -> str:
    population = cast(dict[str, object], report["population"])
    items = cast(list[dict[str, object]], report["items"])
    states = {
        state: sum(item["availability"] == state for item in items)
        for state in ("observed", "error", "missing")
    }
    bound = sum(
        cast(dict[str, object], item["metric"])["semantic_binding"] == "bound" for item in items
    )
    summary = (
        f"Metric occurrences: {population['observed_count']}; bound {bound}; "
        f"scored {states['observed']}; evaluator errors {states['error']}; "
        f"unscored {states['missing']}.\n"
        "Planned evaluations unknown; source claims and owner name meanings unverified. "
        "No statistical or approval authority."
    )
    return (
        summary + " " + AUTO_BOUND_HUMAN_LIMIT
        if report["schema"] == AUTO_RESULT_SCHEMA
        else summary
    )


def _render(report: dict[str, object]) -> str:
    if report["schema"] in (TRACE_SCHEMA, AUTO_TRACE_SCHEMA):
        return render_trace_human(report)
    return (
        render_bound_human(report)
        if report["schema"] in ("evidence_result.v1", AUTO_RESULT_SCHEMA)
        else render_human(report)
    )


def render_trace_human(report: dict[str, object]) -> str:
    """Show only bounded source-claim guidance, without native names or identifiers."""
    actions = cast(list[str], report["next_actions"])
    counts = cast(dict[str, dict[str, object]], report["counts"])
    cases, traces, spans = counts["cases"], counts["traces"], counts["spans"]
    auto_limit = (
        " Metrics and expectation outcomes are not analyzed; "
        "source case success may include unexamined evaluations."
        if report["schema"] == AUTO_TRACE_SCHEMA
        else ""
    )
    return (
        f"Cases: {cases['total']}; traces present {traces['present']}; "
        f"observed spans {spans['total']}; source ERRORED spans {spans['status_errored']}.\n"
        "Case, trace and step statuses are separate source claims. "
        "Planned steps and instrumentation coverage unknown; parent links do not establish "
        "causality, chronology or recovery."
        + auto_limit
        + ("\nNext: " + "; ".join(actions) if actions else "")
    )


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
    if "scored_source_success_absent" in metrics:
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
    if report["schema"] == AUTO_ACCOUNTING_SCHEMA:
        lines.append(AUTO_ORDINARY_HUMAN_LIMIT)
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-deepeval",
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: (
                (AUTO_TRACE_SCHEMA if args.version is None else TRACE_SCHEMA)
                if args.view == "trace-index"
                else (AUTO_RESULT_SCHEMA if args.version is None else "evidence_result.v1")
                if args.bindings is not None
                else AUTO_ACCOUNTING_SCHEMA
                if args.version is None
                else profile_for(args.version).accounting_schema
            ),
            human=_render,
        ),
    )
