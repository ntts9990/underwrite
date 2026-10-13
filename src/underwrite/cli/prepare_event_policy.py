"""Card: policy-preparation | Paper: ADR-020 | Stage: agent-workflow.

Bind explicit consumer choices to a declared baseline population without measurement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, read_validated, run_cli
from underwrite.cli.measure import OBSERVATION_MAX_BYTES, OBSERVATION_MAX_DEPTH, POLICY_MAX_BYTES
from underwrite.instrument.evidence.goppi_baseline import (
    BaselineProjectionError,
    baseline_policy_inputs,
)
from underwrite.measurement.event_policy import EventPolicyError, prepare_event_policy


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Prepare measurement_policy.v3 from a projected baseline exchange v2 declaration "
        "and explicit consumer choices. All assumptions remain unknown; review the draft "
        "before measure. No measurement or storage is performed. "
        f"Output limit: {POLICY_MAX_BYTES} UTF-8 bytes including newline."
    )
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--p0", type=float, required=True)
    parser.add_argument("--ece-bins", type=int, required=True)
    parser.add_argument("--min-bin-n", type=int, required=True)
    parser.add_argument("--all-k", type=int, required=True)
    parser.add_argument("--max-observations", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Emit measurement_policy.v3 JSON.")


def produce(args: argparse.Namespace) -> dict[str, object]:
    observation, _ = read_validated(
        cast(Path, args.observation),
        "observation.v1",
        "/observation",
        OBSERVATION_MAX_BYTES,
        OBSERVATION_MAX_DEPTH,
    )
    try:
        semantics, roster = baseline_policy_inputs(observation)
        return prepare_event_policy(
            semantics,
            roster,
            subject_id=cast(str, args.subject_id),
            p0=cast(float, args.p0),
            ece_bins=cast(int, args.ece_bins),
            min_bin_n=cast(int, args.min_bin_n),
            all_k=cast(int, args.all_k),
            max_observations=cast(int | None, args.max_observations),
        )
    except BaselineProjectionError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location or "/observation") from None
    except EventPolicyError as exc:
        code = "INVALID_INPUT" if exc.code == "INVALID_POLICY" else exc.code
        raise EvidenceError(code, exc.reason, exc.location or "/policy") from None


def _human(policy: dict[str, object]) -> str:
    return (
        "Policy draft prepared; assumptions remain unknown. No measurement or approval.\n"
        + json.dumps(policy, ensure_ascii=True, sort_keys=True, indent=2)
    )


def main(argv: list[str] | None = None) -> int:
    return run_cli(
        "prepare-event-policy",
        argv if argv is not None else [],
        configure,
        produce,
        ReportOutput("measurement_policy.v3", POLICY_MAX_BYTES, human=_human),
    )
