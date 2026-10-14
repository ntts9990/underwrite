"""Audit a Promptfoo export directly while retaining the advanced manifest route."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.ingest.accounting import (
    SOURCE,
    AccountingCaseError,
    audit_promptfoo_file,
    audit_promptfoo_row_index_file,
)
from underwrite.instrument.ingest.codecs.promptfoo import SUPPORTED_VERSIONS


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "file", type=Path, metavar="FILE", help="Local Promptfoo export for the selected version."
    )
    parser.add_argument(
        "--version",
        choices=SUPPORTED_VERSIONS,
        default=SOURCE[1],
        help="Explicit source version; no detection or fallback.",
    )
    parser.add_argument(
        "--view", choices=("accounting", "row-index"), default="accounting",
        help="Select count accounting or a content-minimized ordinal row index.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the selected view as JSON.")
    parser.description = (
        "Compare the selected Promptfoo export's declared counts with its exclusive row tallies. "
        "No manifest or producer execution is needed. An inconsistent report still exits 0. "
        "The profile is selected, not detected; counts provide no approval authority. "
        "The audit-counts CASE.json route uses the manifest's explicit supported version; it adds "
        "expected-hash verification and contained manifest paths."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        audit = audit_promptfoo_row_index_file if args.view == "row-index" else audit_promptfoo_file
        return audit(cast(Path, args.file), version=cast(str, args.version))
    except AccountingCaseError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-promptfoo",
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: "promptfoo_row_index.v1"
            if args.view == "row-index"
            else "promptfoo_accounting.v1"
        ),
    )
