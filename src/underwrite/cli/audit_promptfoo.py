"""Audit a Promptfoo export directly while retaining the advanced manifest route."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import cast

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.evidence import (
    EvidenceError,
    ReportOutput,
    output_limit_error,
    run_cli,
)
from underwrite.cli.local_json import validate
from underwrite.instrument.evidence.promptfoo_compat import (
    ACCOUNTING_SCHEMA,
    ACTIONS_SCHEMA,
    ROW_INDEX_SCHEMA,
)
from underwrite.instrument.ingest.accounting import (
    AccountingCaseError,
    audit_promptfoo_actions_file,
    audit_promptfoo_file,
    audit_promptfoo_row_index_file,
)
from underwrite.instrument.ingest.schema import packaged_validator

SUMMARY_SCHEMA = "promptfoo_accounting_summary.v1"
SUMMARY_MAX_BYTES = 16 * 1024


def _summary_file(path: Path) -> dict[str, object]:
    """Retain direct-file count evidence without row coordinates."""
    return summary_from_actions(
        audit_promptfoo_actions_file(path),
        {
            "kind": "direct_file",
            "manifest_hash": None,
            "expected_hash_check": "not_applicable",
        },
        "audit-promptfoo --view actions",
    )


def summary_from_actions(
    full: dict[str, object], source_binding: dict[str, object], full_view: str
) -> dict[str, object]:
    """Project the validated Promptfoo actions owner with explicit read binding."""
    validate(packaged_validator(ACTIONS_SCHEMA), full, EvidenceError, "INTERNAL_ERROR")
    validator = packaged_validator(SUMMARY_SCHEMA)
    schema = cast(dict[str, object], validator.schema)
    properties = cast(dict[str, object], schema["properties"])
    counts_schema = cast(dict[str, object], properties["finding_counts"])
    codes = cast(list[str], counts_schema["required"])
    findings = cast(list[dict[str, object]], full["findings"])
    totals = Counter(cast(str, item["code"]) for item in findings)
    summary: dict[str, object] = {
        "schema": SUMMARY_SCHEMA,
        "view": "summary",
        **{
            name: full[name]
            for name in (
                "source", "raw_hash", "reader_profile", "scope", "producer_stats",
                "row_tallies", "deltas", "declared_relation", "limitations", "next_actions",
            )
        },
        "finding_counts": {code: totals[code] for code in codes},
        "source_binding": source_binding,
        "detail_state": {
            "row_coordinates": "intentionally_omitted",
            "source_text": "intentionally_omitted",
            "full_view": full_view,
        },
    }
    validate(validator, summary, EvidenceError, "INTERNAL_ERROR")
    if len(canonical_bytes(summary)) + 1 > SUMMARY_MAX_BYTES:
        raise output_limit_error()
    return summary

VIEWS = {
    "accounting": (audit_promptfoo_file, ACCOUNTING_SCHEMA),
    "row-index": (audit_promptfoo_row_index_file, ROW_INDEX_SCHEMA),
    "actions": (audit_promptfoo_actions_file, ACTIONS_SCHEMA),
    "summary": (_summary_file, SUMMARY_SCHEMA),
}


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, metavar="FILE", help="Local Promptfoo SummaryV3 export.")
    parser.add_argument(
        "--view",
        choices=tuple(VIEWS),
        default="accounting",
        help="Select count accounting, a row index, next actions, or a bounded summary.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the selected view as JSON.")
    parser.description = (
        "Compare the selected Promptfoo export's declared counts with its exclusive row tallies. "
        "No manifest or producer execution is needed. An inconsistent report still exits 0. "
        "Read SummaryV3 selected fields and report any producer-version "
        "declaration as an unverified source claim. Other source fields are not analyzed. "
        "Counts provide no approval authority. "
        "The audit-counts CASE.json route uses the manifest's explicit supported version; it adds "
        "expected-hash verification and contained manifest paths."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        audit, _ = VIEWS[args.view]
        return audit(cast(Path, args.file))
    except AccountingCaseError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-promptfoo",
        argv,
        configure,
        produce,
        ReportOutput(lambda args: VIEWS[args.view][1]),
    )
