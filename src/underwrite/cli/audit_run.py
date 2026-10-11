"""Audit a neutral binary run inventory or the exact experimental goppi exchange."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, read_validated, run_cli
from underwrite.instrument.evidence.run_audit import (
    INPUT_MAX_BYTES,
    OUTPUT_MAX_BYTES,
    RunAuditError,
    audit_run_inventory,
)
from underwrite.instrument.ingest.goppi_pilot import GoppiPilotError, load_inventory


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Audit bounded producer execution units without comparison or inference. "
        "The goppi option is an exact experimental repository-study profile, not generic "
        "goppi compatibility."
    )
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--input", help="Local binary_run_inventory.v1 JSON file.")
    sources.add_argument(
        "--goppi-grounded-pilot-exchange",
        metavar="DIR",
        help="Exact experimental goppi grounded-pilot exchange directory.",
    )
    parser.add_argument("--json", action="store_true", help="Emit one typed JSON report.")


def _inventory(args: argparse.Namespace) -> dict[str, object]:
    input_path = cast(str | None, args.input)
    if input_path is not None:
        inventory, _ = read_validated(
            Path(input_path), "binary_run_inventory.v1", "/input", INPUT_MAX_BYTES, 32
        )
        return cast(dict[str, object], inventory)
    try:
        return load_inventory(Path(cast(str, args.goppi_grounded_pilot_exchange)))
    except GoppiPilotError as exc:
        code = (
            exc.code
            if exc.code in {"SOURCE_READ_FAILED", "INPUT_LIMIT_EXCEEDED", "UNSUPPORTED_PROFILE"}
            else "INVALID_INPUT"
        )
        raise EvidenceError(
            code,
            exc.reason,
            exc.location or "/goppi-grounded-pilot-exchange",
        ) from exc


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        return audit_run_inventory(_inventory(args))
    except RunAuditError as exc:
        code = (
            exc.code
            if exc.code in {"INPUT_LIMIT_EXCEEDED", "UNSUPPORTED_PROFILE"}
            else "INVALID_INPUT"
        )
        raise EvidenceError(code, exc.code, "/input") from exc


def main(argv: list[str] | None = None) -> int:
    return run_cli(
        "audit-run",
        argv if argv is not None else [],
        configure,
        produce,
        "binary_run_audit.v1",
        output_max_bytes=OUTPUT_MAX_BYTES,
    )
