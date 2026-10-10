"""One pinned Promptfoo count audit; a produced report is never approval."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, run_cli
from underwrite.instrument.ingest.accounting import AccountingCaseError, audit_case


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "case", type=Path, metavar="CASE.json", help="Local artifact_case.v1 manifest."
    )
    parser.add_argument(
        "--json", action="store_true", help="Canonical accounting report on stdout."
    )
    parser.description = (
        "Compare Promptfoo 0.123.0 summary counts with exclusive result-row tallies. "
        "A produced inconsistent report still exits 0; this checks source-internal agreement, "
        "not planned inventory, execution truth, measurement, or approval."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        return audit_case(cast(Path, args.case))
    except AccountingCaseError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from exc


def main(argv: list[str]) -> int:
    return run_cli("audit-counts", argv, configure, produce, "promptfoo_accounting.v1")
