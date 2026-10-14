"""Audit a completed goppi baseline exchange as descriptive common evidence."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, run_cli
from underwrite.instrument.evidence.goppi_baseline import BaselineProjectionError
from underwrite.instrument.ingest.goppi_baseline import (
    audit_baseline_exchange,
)
from underwrite.instrument.ingest.local_capture import CaptureError


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Audit the exact goppi baseline exchange v2 as descriptive source claims. "
        "No statistical direction, independent provenance, or approval is inferred."
    )
    parser.add_argument("--exchange", type=Path, required=True, metavar="DIR")
    parser.add_argument("--json", action="store_true", help="Emit evidence_result.v1 JSON.")


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        return audit_baseline_exchange(cast(Path, args.exchange))
    except (CaptureError, BaselineProjectionError) as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location or "/exchange") from None


def main(argv: list[str]) -> int:
    return run_cli("audit-baseline", argv, configure, produce, "evidence_result.v1")
