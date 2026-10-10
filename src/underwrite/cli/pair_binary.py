"""Explicit paired binary evidence command; it makes no release decision."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli.evidence import EvidenceError, read_validated, run_cli
from underwrite.instrument.evidence.paired_binary import (
    INPUT_MAX_BYTES,
    OUTPUT_MAX_BYTES,
    PairedBinaryError,
    evaluate_paired_binary,
)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Compute bounded paired binary evidence from declared primary repeat-zero pairs. "
        "Source outcomes and assumptions remain unverified; this is not approval."
    )
    parser.add_argument("--input", required=True, help="Local paired_binary_input.v1 JSON file.")
    parser.add_argument(
        "--policy", required=True, help="Explicit paired_binary_policy.v1 JSON file."
    )
    parser.add_argument("--json", action="store_true", help="Emit one typed JSON report on stdout.")


def _produce(args: argparse.Namespace) -> dict[str, object]:
    input_doc, input_digest = read_validated(
        Path(cast(str, args.input)), "paired_binary_input.v1", "/input", INPUT_MAX_BYTES, 32
    )
    policy_doc, policy_digest = read_validated(
        Path(cast(str, args.policy)), "paired_binary_policy.v1", "/policy", 64 * 1024, 16
    )
    try:
        report = evaluate_paired_binary(input_doc, policy_doc)
    except PairedBinaryError as exc:
        if exc.code in {
            "COMPUTATION_LIMIT_EXCEEDED",
            "INPUT_LIMIT_EXCEEDED",
            "UNSUPPORTED_PROFILE",
        }:
            location = "/policy/max_work" if exc.code == "COMPUTATION_LIMIT_EXCEEDED" else "/input"
            raise EvidenceError(exc.code, exc.code, location) from exc
        raise EvidenceError("INVALID_INPUT", exc.code, "/input") from exc
    if report["input_digest"] != input_digest or report["policy_digest"] != policy_digest:
        raise EvidenceError("INTERNAL_ERROR", "DIGEST_DISAGREEMENT")
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the bounded standalone command; root registers this entry point."""
    return run_cli(
        "pair-binary",
        argv if argv is not None else [],
        configure,
        _produce,
        "paired_binary_evidence.v1",
        output_max_bytes=OUTPUT_MAX_BYTES,
    )
