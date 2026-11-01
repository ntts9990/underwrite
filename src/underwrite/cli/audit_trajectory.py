"""Card: trajectory-consumer | Paper: goppi paired trajectory v2 | Stage: producer-bridge.

Describe an exported trajectory through a configured offline verifier port.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.evidence.goppi_trajectory import (
    MAX_REPORT_BYTES,
    OUTCOME_ACTIONS_SCHEMA_V2,
    RESULT_SCHEMA_V2,
    TrajectoryError,
)
from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.goppi_trajectory import (
    TrajectoryInputError,
    audit_goppi_trajectory,
)
from underwrite.instrument.ingest.goppi_verifier import VerifierError
from underwrite.instrument.ingest.local_capture import CaptureError

RESULT_SCHEMA = RESULT_SCHEMA_V2
OUTCOME_ACTIONS_SCHEMA = OUTCOME_ACTIONS_SCHEMA_V2


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Compare recorded tool statuses and verifier outcomes by case, stage and repeat "
        "in a completed Goppi paired+trajectory export. Consumes the configured offline "
        "verify-paired report over a private copy of the captured bytes. "
        "Verifier identity and its content-closure work are not independently proven. "
        "Underwrite requests no evaluation or model call and grants no approval."
    )
    parser.epilog = (
        "Goppi must already be installed with paired trajectory verification support; "
        "nothing is installed automatically. Supply the complete outer export directory "
        "using a physical path without symlink components. "
        "Missing capture stays unknown, not zero. "
        "Differences do not establish call correspondence, retry benefit or causality. "
        "Exit 0 means a descriptive report was produced from a configured verifier report, "
        "not native authentication, an improved agent or approval. "
        "Keep the original export to resolve the report's evidence references."
    )
    parser.add_argument("--exchange", type=Path, required=True, metavar="DIR")
    parser.add_argument(
        "--view",
        choices=("review", "outcome-actions"),
        default="review",
        help="Select the default review or direct outcome-actions report.",
    )
    parser.add_argument(
        "--goppi-executable",
        type=Path,
        metavar="PATH",
        help="Operator-configured Goppi executable; its identity is not independently verified.",
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
        return audit_goppi_trajectory(
            cast(Path, args.exchange),
            goppi_executable=cast(Path | None, args.goppi_executable),
            view=cast(str, args.view),
        )
    except (CaptureError, TrajectoryInputError, TrajectoryError, VerifierError) as exc:
        raise trajectory_cli_error(exc) from None


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-trajectory",
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: (
                OUTCOME_ACTIONS_SCHEMA if args.view == "outcome-actions" else RESULT_SCHEMA
            ),
            max_bytes=MAX_REPORT_BYTES,
        ),
    )
