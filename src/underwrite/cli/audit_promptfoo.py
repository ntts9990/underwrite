"""Audit a Promptfoo export directly while retaining the advanced manifest route."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
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

VIEWS = {
    "accounting": (audit_promptfoo_file, ACCOUNTING_SCHEMA),
    "row-index": (audit_promptfoo_row_index_file, ROW_INDEX_SCHEMA),
    "actions": (audit_promptfoo_actions_file, ACTIONS_SCHEMA),
}


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, metavar="FILE", help="Local Promptfoo SummaryV3 export.")
    parser.add_argument(
        "--view",
        choices=tuple(VIEWS),
        default="accounting",
        help="Select count accounting, a content-minimized ordinal row index, or next actions.",
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
