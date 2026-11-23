"""One-file DeepEval metric accounting with explicit source-claim limits."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.evidence import (
    EvidenceError,
    ReportOutput,
    output_limit_error,
    run_cli,
    usage_error,
)
from underwrite.cli.inspect_result import inspection_vocabulary
from underwrite.cli.local_json import validate
from underwrite.instrument.evidence.common_result import (
    ResultInvariantError,
    availability_counts,
    check_result_invariants,
    item_histograms,
)
from underwrite.instrument.evidence.deepeval_binding import (
    AUTO_RESULT_SCHEMA,
    DeepevalBindingError,
)
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_SCHEMA as AUTO_ACCOUNTING_SCHEMA,
)
from underwrite.instrument.evidence.deepeval_compat import TRACE_SCHEMA as AUTO_TRACE_SCHEMA
from underwrite.instrument.ingest.deepeval_accounting import (
    DeepevalFileError,
    audit_deepeval_file,
)
from underwrite.instrument.ingest.deepeval_binding import audit_deepeval_bound_file
from underwrite.instrument.ingest.deepeval_trace import audit_deepeval_trace_file
from underwrite.instrument.ingest.schema import packaged_validator

SUMMARY_SCHEMA = "deepeval_accounting_summary.v1"
SUMMARY_MAX_BYTES = 16 * 1024
TRACE_SUMMARY_SCHEMA = "deepeval_trace_summary.v1"
TRACE_SUMMARY_MAX_BYTES = 16 * 1024
TRACE_SUMMARY_LINKABILITY = ("SOURCE_HASH_AND_POINTERS_LINKABLE", "SOURCE_HASH_LINKABLE")
BOUND_SUMMARY_SCHEMA = "deepeval_bound_summary.v1"
BOUND_SUMMARY_MAX_BYTES = 16 * 1024
BOUND_SUMMARY_LIMITATIONS = (
    "SOURCE_CLAIMS_UNVERIFIED",
    "OWNER_METRIC_BINDINGS_ASSERTED_UNVERIFIED",
    "SOURCE_BYTES_WITHOUT_EXTERNAL_EXPECTED_HASH",
    "PLANNED_POPULATION_UNKNOWN",
    "ITEM_DETAILS_INTENTIONALLY_OMITTED",
    "VERIFICATION_REFS_NOT_REPERFORMED",
    "NO_MEASUREMENT_OR_APPROVAL_AUTHORITY",
)

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
        "--bindings",
        type=Path,
        default=None,
        help=("Owner-asserted format-only deepeval_metric_bindings.v2 pack."),
    )
    parser.add_argument(
        "--view",
        choices=("accounting", "trace-index", "summary", "trace-summary", "bound-summary"),
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
        "A selected local reader does not establish a producer release. "
        "If missing-execution accounting is required, the inventory next action asks for "
        "external planned evidence from the evaluation owner. This command has no inventory "
        "input; planned and missing-execution counts remain unknown."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        if args.view == "bound-summary":
            if args.bindings is None:
                raise usage_error()
            return _bound_summary_file(cast(Path, args.file), cast(Path, args.bindings))
        if args.view in {"trace-index", "trace-summary"}:
            if args.bindings is not None:
                raise EvidenceError(
                    "UNSUPPORTED_FORMAT", "DEEPEVAL_TRACE_BINDINGS_UNSUPPORTED", "/bindings"
                )
            return (
                _trace_summary_file(cast(Path, args.file))
                if args.view == "trace-summary"
                else audit_deepeval_trace_file(cast(Path, args.file))
            )
        if args.view == "summary":
            if args.bindings is not None:
                raise EvidenceError(
                    "UNSUPPORTED_FORMAT", "DEEPEVAL_SUMMARY_BINDINGS_UNSUPPORTED", "/bindings"
                )
            return _summary_file(cast(Path, args.file))
        if args.bindings is not None:
            return audit_deepeval_bound_file(cast(Path, args.file), cast(Path, args.bindings))
        return audit_deepeval_file(cast(Path, args.file))
    except (DeepevalFileError, DeepevalBindingError) as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def _summary_file(path: Path) -> dict[str, object]:
    full = audit_deepeval_file(path)
    validate(packaged_validator(AUTO_ACCOUNTING_SCHEMA), full, EvidenceError, "INTERNAL_ERROR")
    summary = {
        name: value for name, value in full.items() if name != "cases"
    }
    summary["schema"] = SUMMARY_SCHEMA
    summary["view"] = "summary"
    summary["detail_state"] = {
        "cases": "intentionally_omitted",
        "metric_names": "intentionally_omitted",
        "evaluator_errors": "intentionally_omitted",
        "full_view": "audit-deepeval --view accounting",
    }
    validate(packaged_validator(SUMMARY_SCHEMA), summary, EvidenceError, "INTERNAL_ERROR")
    if len(canonical_bytes(summary)) + 1 > SUMMARY_MAX_BYTES:
        raise output_limit_error()
    return summary


def _trace_summary_file(path: Path) -> dict[str, object]:
    full = audit_deepeval_trace_file(path)
    validate(packaged_validator(AUTO_TRACE_SCHEMA), full, EvidenceError, "INTERNAL_ERROR")
    summary = {name: value for name, value in full.items() if name != "cases"}
    summary["schema"] = TRACE_SUMMARY_SCHEMA
    summary["view"] = "trace-summary"
    full_linkability, summary_linkability = TRACE_SUMMARY_LINKABILITY
    summary["limitations"] = [
        summary_linkability if item == full_linkability else item
        for item in cast(list[str], full["limitations"])
    ]
    summary["detail_state"] = {
        "case_coordinates": "intentionally_omitted",
        "span_coordinates": "intentionally_omitted",
        "parent_pointers": "intentionally_omitted",
        "full_view": "audit-deepeval --view trace-index",
    }
    validate(packaged_validator(TRACE_SUMMARY_SCHEMA), summary, EvidenceError, "INTERNAL_ERROR")
    if len(canonical_bytes(summary)) + 1 > TRACE_SUMMARY_MAX_BYTES:
        raise output_limit_error()
    return summary


def _bound_summary_file(path: Path, bindings_path: Path) -> dict[str, object]:
    full = audit_deepeval_bound_file(path, bindings_path)
    validator = packaged_validator(AUTO_RESULT_SCHEMA)
    validate(validator, full, EvidenceError, "INTERNAL_ERROR")
    try:
        check_result_invariants(full)
    except ResultInvariantError:
        raise EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR") from None
    action_names, binding_names = inspection_vocabulary(validator.schema)
    available = availability_counts(full)
    action_counts, binding_counts = item_histograms(full, action_names, binding_names)
    if available is None or action_counts is None or binding_counts is None:
        raise EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR")
    verification = cast(list[object], full["verification"])
    summary: dict[str, object] = {
        "schema": BOUND_SUMMARY_SCHEMA,
        "view": "bound-summary",
        "source": full["source"],
        "declaration_source": full["declaration_source"],
        "processing": full["processing"],
        "population": full["population"],
        "availability_counts": available,
        "item_next_action_counts": action_counts,
        "declared_semantic_binding_counts": binding_counts,
        "verification_entry_count": len(verification),
        "limitations": list(BOUND_SUMMARY_LIMITATIONS),
        "detail_state": {
            "item_values": "intentionally_omitted",
            "item_coordinates": "intentionally_omitted",
            "metric_names": "intentionally_omitted",
            "full_view": "audit-deepeval --bindings PACK",
        },
    }
    validate(packaged_validator(BOUND_SUMMARY_SCHEMA), summary, EvidenceError, "INTERNAL_ERROR")
    if len(canonical_bytes(summary)) + 1 > BOUND_SUMMARY_MAX_BYTES:
        raise output_limit_error()
    return summary


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
    return summary + " " + AUTO_BOUND_HUMAN_LIMIT


def _render(report: dict[str, object]) -> str:
    if report["schema"] == BOUND_SUMMARY_SCHEMA:
        population = cast(dict[str, object], report["population"])
        states = cast(dict[str, int], report["availability_counts"])
        bindings = cast(dict[str, int], report["declared_semantic_binding_counts"])
        return (
            f"Supplied metric occurrences: {population['observed_count']}; "
            f"scored {states['observed']}; evaluator errors {states['error']}; "
            f"unscored {states['missing']}; owner-bound {bindings['bound']}. "
            "Owner assertions and source claims remain unverified; no approval authority."
        )
    if report["schema"] in {AUTO_TRACE_SCHEMA, TRACE_SUMMARY_SCHEMA}:
        return render_trace_human(report)
    return (
        render_bound_human(report)
        if report["schema"] == AUTO_RESULT_SCHEMA
        else render_human(report)
    )


def render_trace_human(report: dict[str, object]) -> str:
    """Show only bounded source-claim guidance, without native names or identifiers."""
    actions = cast(list[str], report["next_actions"])
    counts = cast(dict[str, dict[str, object]], report["counts"])
    cases, traces, spans = counts["cases"], counts["traces"], counts["spans"]
    return (
        f"Cases: {cases['total']}; traces present {traces['present']}; "
        f"observed spans {spans['total']}; source ERRORED spans {spans['status_errored']}.\n"
        "Case, trace and step statuses are separate source claims. "
        "Planned steps and instrumentation coverage unknown; parent links do not establish "
        "causality, chronology or recovery. "
        "Metrics and expectation outcomes are not analyzed; "
        "source case success may include unexamined evaluations."
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
                AUTO_TRACE_SCHEMA
                if args.view == "trace-index"
                else TRACE_SUMMARY_SCHEMA
                if args.view == "trace-summary"
                else BOUND_SUMMARY_SCHEMA
                if args.view == "bound-summary"
                else SUMMARY_SCHEMA
                if args.view == "summary"
                else AUTO_RESULT_SCHEMA
                if args.bindings is not None
                else AUTO_ACCOUNTING_SCHEMA
            ),
            human=_render,
        ),
    )
