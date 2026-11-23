"""One explicitly selected Promptfoo manifest count audit; a produced report is never approval."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.audit_promptfoo import SUMMARY_SCHEMA, summary_from_actions
from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.evidence.promptfoo_compat import ACCOUNTING_SCHEMA
from underwrite.instrument.ingest.accounting import (
    AccountingCaseError,
    audit_case,
    audit_case_actions_with_inspection,
)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "case", type=Path, metavar="CASE.json", help="Local artifact_case.v1 manifest."
    )
    parser.add_argument(
        "--json", action="store_true", help="Canonical accounting report on stdout."
    )
    parser.add_argument(
        "--view", choices=("full", "summary"), default="full",
        help="Return full accounting or bounded action counts with manifest binding.",
    )
    parser.description = (
        "Compare Promptfoo 0.123.0 or 0.124.1 summary counts with exclusive result-row tallies. "
        "The manifest selects the profile and source identity; any supplied expected hash "
        "is checked against the same contained bytes that are counted. "
        "A produced inconsistent report still exits 0; this checks source-internal agreement, "
        "not planned inventory, execution truth, measurement, or approval."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        if args.view == "summary":
            actions, inspection = audit_case_actions_with_inspection(cast(Path, args.case))
            return summary_from_actions(
                actions,
                {
                    "kind": "manifest_case",
                    "manifest_hash": inspection["manifest_hash"],
                    "expected_hash_check": inspection["expected_hash_check"],
                },
                "audit-counts --view full",
            )
        return audit_case(cast(Path, args.case))
    except AccountingCaseError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from exc


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-counts",
        argv,
        configure,
        produce,
        ReportOutput(lambda args: SUMMARY_SCHEMA if args.view == "summary" else ACCOUNTING_SCHEMA),
    )
