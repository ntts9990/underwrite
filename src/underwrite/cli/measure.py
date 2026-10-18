"""Two bounded local inputs and one current event measurement flow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, NoReturn

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.evidence import display
from underwrite.cli.local_json import Validator as Validator
from underwrite.cli.local_json import read_document
from underwrite.cli.local_json import validate as validate
from underwrite.cli.measurement_flow import (
    OBSERVATION_MAX_BYTES,
    OBSERVATION_MAX_DEPTH,
    OUTPUT_MAX_BYTES,
    POLICY_MAX_BYTES,
    POLICY_MAX_DEPTH,
    InputRole,
    MeasurementError,
    input_limits,
    measurement_error,
    read_measurement,
)
from underwrite.cli.measurement_flow import InputReader as InputReader
from underwrite.cli.measurement_flow import measurement_validators as measurement_validators
from underwrite.instrument.ingest.schema import SchemaConfigurationError as SchemaConfigurationError
from underwrite.measurement.event_read import EventReadError as EventReadError
from underwrite.measurement.read_input import ReadInputError as ReadInputError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise MeasurementError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP")


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--observation",
        required=True,
        type=Path,
        help="Local observation.v1 from underwrite.evidence-bundle v1.",
    )
    parser.add_argument(
        "--policy",
        required=True,
        type=Path,
        help="Profile binary-event-directional.v1 in complete measurement_policy.v3.",
    )
    parser.add_argument("--json", action="store_true", help="Canonical read on stdout.")
    parser.description = (
        "Measure only native underwrite.evidence-bundle v1 eval_run observations. "
        "Selected rows and availability records require epoch=null and repeat=0. "
        "External observation profiles are unsupported (UNSUPPORTED_PROFILE with a valid "
        "policy); no read is produced. Exit 0 means a read was produced, not approval. "
        f"Observation limits: {OBSERVATION_MAX_BYTES} bytes / depth {OBSERVATION_MAX_DEPTH}; "
        f"policy: {POLICY_MAX_BYTES} bytes / depth {POLICY_MAX_DEPTH}; "
        f"output: {OUTPUT_MAX_BYTES} bytes including newline."
    )


def _read(args: argparse.Namespace) -> dict[str, Any]:
    def reader(role: InputRole) -> tuple[dict[str, Any], str]:
        max_bytes, max_depth = input_limits(role)
        return read_document(
            getattr(args, role),
            max_bytes,
            max_depth,
            "/" + role,
            "INVALID_" + role.upper(),
            MeasurementError,
        )

    return read_measurement(reader)


def _human(read: dict[str, Any]) -> str:
    lines = ["Measurement completed; exit 0 is not approval. Source claims remain unverified."]
    for key in (
        "subject",
        "semantic_digest",
        "verdict",
        "decision",
        "reason_codes",
        "population",
        "directional",
        "diagnostics",
        "limitations",
        "sources",
    ):
        lines.append(f"{key}: " + display(read[key]))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = _Parser(prog="underwrite measure", allow_abbrev=False)
    configure(parser)
    try:
        args = parser.parse_args(argv)
        read = _read(args)
        output = (canonical_bytes(read).decode() if args.json else _human(read)) + "\n"
        if len(output.encode()) > OUTPUT_MAX_BYTES:
            raise MeasurementError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    except Exception as exc:
        error = measurement_error(exc)
    else:
        sys.stdout.write(output)
        return 0
    sys.stderr.write(canonical_bytes(error.payload).decode() + "\n")
    return 2
