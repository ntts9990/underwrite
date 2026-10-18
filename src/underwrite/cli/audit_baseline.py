"""Audit a completed goppi baseline exchange as descriptive common evidence."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, run_cli
from underwrite.instrument.evidence.goppi_baseline import RESULT_SCHEMA, BaselineProjectionError
from underwrite.instrument.ingest.goppi_baseline import (
    audit_baseline_exchange,
)
from underwrite.instrument.ingest.local_capture import CaptureError


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Audit the exact goppi baseline exchange v2 as descriptive source claims. "
        "No statistical direction, independent provenance, or approval is inferred."
    )
    parser.epilog = (
        "Each item's coordinate.index is the zero-based position in scope.json's "
        "ordered selection.case_ids array. Keep the source directory to resolve "
        "original case IDs or bytes; hashes identify bytes, not retrieval locations. "
        "Case IDs and private text are omitted from the result."
    )
    parser.add_argument("--exchange", type=Path, required=True, metavar="DIR")
    parser.add_argument("--json", action="store_true", help=f"Emit {RESULT_SCHEMA} JSON.")


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        return audit_baseline_exchange(cast(Path, args.exchange))
    except (CaptureError, BaselineProjectionError) as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location or "/exchange") from None


def main(argv: list[str]) -> int:
    return run_cli("audit-baseline", argv, configure, produce, RESULT_SCHEMA)
