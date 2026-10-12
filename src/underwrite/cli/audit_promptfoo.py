"""Audit a Promptfoo export directly while retaining the advanced manifest route."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, run_cli
from underwrite.instrument.ingest.accounting import AccountingCaseError, audit_promptfoo_file


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, metavar="FILE", help="Local Promptfoo 0.123.0 export.")
    parser.add_argument("--json", action="store_true", help="Emit promptfoo_accounting.v1 JSON.")
    parser.description = (
        "Compare a Promptfoo 0.123.0 export's declared counts with its exclusive row tallies. "
        "No manifest or producer execution is needed. An inconsistent report still exits 0. "
        "The profile is selected, not detected; counts provide no approval authority. "
        "Use audit-counts CASE.json for expected-hash verification and contained manifest paths."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        return audit_promptfoo_file(cast(Path, args.file))
    except AccountingCaseError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def main(argv: list[str]) -> int:
    return run_cli("audit-promptfoo", argv, configure, produce, "promptfoo_accounting.v1")
