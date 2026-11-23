"""Compare declared common-result marginals without inventing item correspondence."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.evidence import (
    EvidenceError,
    ReportOutput,
    output_limit_error,
    run_cli,
)
from underwrite.cli.inspect_result import inspect_result_file
from underwrite.cli.local_json import validate
from underwrite.instrument.evidence.common_result import (
    POPULATION_UNIT_MISMATCH,
    RESULT_FORMAT_MISMATCH,
    RESULT_NOT_ANALYZED,
    compare_result_marginals,
)
from underwrite.instrument.ingest.schema import packaged_validator

RESULT_SCHEMA = "common_result_marginal_comparison.v1"
OUTPUT_MAX_BYTES = 16 * 1024
LIMITATIONS = [
    "SOURCE_CLAIMS_UNVERIFIED",
    "MARGINAL_DELTAS_NOT_ITEM_TRANSITIONS",
    "EQUAL_TOTALS_NOT_ITEM_IDENTITY",
    "ITEM_CORRESPONDENCE_NOT_ESTABLISHED",
    "SEMANTIC_COMPARABILITY_UNESTABLISHED",
    "SELECTED_FILES_NOT_ATOMIC_PAIR",
    "NO_MEASUREMENT_OR_ACCEPTANCE",
    "RAW_HASH_AND_COUNTS_LINKABLE",
]
COMPUTED_TASKS = [
    {"code": "ESTABLISH_CROSS_RESULT_ITEM_BINDING", "owner": "evaluation_owner"},
    {"code": "VERIFY_POPULATION_AND_METRIC_SEMANTICS", "owner": "evaluation_owner"},
]
INELIGIBLE_TASKS = {
    RESULT_NOT_ANALYZED: [{"code": "RESOLVE_SOURCE_PROCESSING", "owner": "producer_owner"}],
    RESULT_FORMAT_MISMATCH: [
        {"code": "SELECT_COMPATIBLE_RESULT_PAIR", "owner": "calling_agent"}
    ],
    POPULATION_UNIT_MISMATCH: [
        {"code": "SELECT_COMPATIBLE_RESULT_PAIR", "owner": "calling_agent"}
    ],
}


def compare_result_files(baseline: Path, candidate: Path) -> dict[str, object]:
    """Read two local results once each and report only descriptive marginals."""
    before = inspect_result_file(baseline, view="actions")
    after = inspect_result_file(candidate, view="actions")
    marginal = compare_result_marginals(before, after)
    reason = marginal["reason"]
    tasks = COMPUTED_TASKS if reason is None else INELIGIBLE_TASKS[cast(str, reason)]
    report: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "selected_results": {
            "baseline": before["selected_result"],
            "candidate": after["selected_result"],
        },
        "declared_processing": {
            "baseline": before["declared_processing"],
            "candidate": after["declared_processing"],
        },
        "declared_population": {
            "baseline": before["declared_population"],
            "candidate": after["declared_population"],
        },
        "marginal_calculation": {"direction": "candidate_minus_baseline", **marginal},
        "item_correspondence": {
            "state": "not_established",
            "reason": "CROSS_RESULT_ITEM_BINDING_NOT_SUPPLIED",
        },
        "semantic_comparability": "unestablished",
        "next_verification_tasks": [task.copy() for task in tasks],
        "limitations": LIMITATIONS.copy(),
    }
    validate(packaged_validator(RESULT_SCHEMA), report, EvidenceError, "INTERNAL_ERROR")
    if len(canonical_bytes(report)) + 1 > OUTPUT_MAX_BYTES:
        raise output_limit_error()
    return report


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Compare declared marginal counts from two selected common-result files. "
        "Deltas do not establish item transitions, semantic equivalence, improvement or approval."
    )
    parser.add_argument("--baseline", required=True, type=Path, metavar="FILE")
    parser.add_argument("--candidate", required=True, type=Path, metavar="FILE")
    parser.add_argument("--json", action="store_true", help="Emit typed JSON.")


def produce(args: argparse.Namespace) -> dict[str, object]:
    return compare_result_files(cast(Path, args.baseline), cast(Path, args.candidate))


def main(argv: list[str]) -> int:
    return run_cli(
        "compare-results", argv, configure, produce, ReportOutput(RESULT_SCHEMA, OUTPUT_MAX_BYTES)
    )
