"""Card: trajectory-consumer | Paper: goppi paired trajectory v1 | Stage: producer-bridge.

Describe an exported trajectory through the producer's offline verification port.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.evidence.goppi_trajectory import (
    MAX_REPORT_BYTES,
    RESULT_SCHEMA,
    TrajectoryError,
)
from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.goppi_trajectory import (
    TrajectoryInputError,
    audit_goppi_trajectory,
)
from underwrite.instrument.ingest.goppi_verifier import GoppiCliVerifier, VerifierError
from underwrite.instrument.ingest.local_capture import CaptureError


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Compare recorded tool statuses and verifier outcomes by case, stage and repeat "
        "in a completed Goppi paired+trajectory export. Uses the installed Goppi offline "
        "verify-paired command on a private copy of the exact captured bytes. "
        "No evaluation, model call, source modification or approval."
    )
    parser.epilog = (
        "Goppi must already be installed with paired trajectory verification support; "
        "nothing is installed automatically. Supply the complete outer export directory "
        "using a physical path without symlink components. "
        "Missing capture stays unknown, not zero. "
        "Differences do not establish call correspondence, retry benefit or causality. "
        "Exit 0 means a descriptive report was produced, not an improved or approved agent. "
        "Keep the original export to resolve the report's evidence references."
    )
    parser.add_argument("--exchange", type=Path, required=True, metavar="DIR")
    parser.add_argument(
        "--goppi-executable",
        type=Path,
        metavar="PATH",
        help="Trusted installed Goppi executable; defaults to goppi on PATH.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the trajectory review JSON.")


def trajectory_cli_error(
    error: CaptureError | TrajectoryInputError | TrajectoryError | VerifierError,
) -> EvidenceError:
    """Map trajectory failures to the option or artifact the caller can correct."""
    if error.reason == "GOPPI_VERIFIER_UNAVAILABLE":
        return EvidenceError(
            error.code, error.reason, "/goppi_executable", NEXT_ACTIONS["USAGE_ERROR"]
        )
    location = "/verifier" if isinstance(error, VerifierError) else error.location or "/exchange"
    return EvidenceError(error.code, error.reason, location)


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        verifier = GoppiCliVerifier(cast(Path | None, args.goppi_executable))
        return audit_goppi_trajectory(cast(Path, args.exchange), verifier=verifier)
    except (CaptureError, TrajectoryInputError, TrajectoryError, VerifierError) as exc:
        raise trajectory_cli_error(exc) from None


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-trajectory",
        argv,
        configure,
        produce,
        ReportOutput(RESULT_SCHEMA, max_bytes=MAX_REPORT_BYTES),
    )
