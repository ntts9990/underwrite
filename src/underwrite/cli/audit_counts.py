"""One explicitly selected Promptfoo manifest count audit; a produced report is never approval."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, run_cli
from underwrite.instrument.evidence.promptfoo_compat import ACCOUNTING_SCHEMA
from underwrite.instrument.ingest.accounting import AccountingCaseError, audit_case


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "case", type=Path, metavar="CASE.json", help="Local artifact_case.v1 manifest."
    )
    parser.add_argument(
        "--json", action="store_true", help="Canonical accounting report on stdout."
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
        return audit_case(cast(Path, args.case))
    except AccountingCaseError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from exc


def main(argv: list[str]) -> int:
    return run_cli("audit-counts", argv, configure, produce, ACCOUNTING_SCHEMA)
