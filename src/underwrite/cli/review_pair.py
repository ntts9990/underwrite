"""Card: paired-evidence | Paper: declared-comparison | Stage: evidence-pilot.

Bind declared review context to one exact paired input and computation policy.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite.cli import pair_binary
from underwrite.cli.evidence import EvidenceError, read_validated, run_cli
from underwrite.cli.local_json import validate
from underwrite.instrument.evidence.paired_binary import PairedBinaryError
from underwrite.instrument.evidence.paired_review import (
    MAX_CONTEXT_BYTES,
    PairedReviewError,
    evaluate_paired_review,
)
from underwrite.instrument.ingest.schema import packaged_validator


def configure(parser: argparse.ArgumentParser) -> None:
    pair_binary.configure(parser)
    parser.description = (
        "Review declared changes and invariants bound to a paired input and policy. "
        "The output references the independently computed paired evidence; "
        "declarations do not verify actual conditions or authorize a change."
    )
    parser.add_argument("--context", required=True, help="Local paired_review_context.v1 JSON.")


def produce(args: argparse.Namespace) -> dict[str, object]:
    input_doc, policy_doc, input_digest, policy_digest = pair_binary.read_inputs(args)
    context, _ = read_validated(
        Path(cast(str, args.context)), "paired_review_context.v1", "/context", MAX_CONTEXT_BYTES, 16
    )
    try:
        evidence, review = evaluate_paired_review(input_doc, policy_doc, context)
    except PairedReviewError as exc:
        raise EvidenceError(exc.code, exc.reason, "/context") from exc
    except PairedBinaryError as exc:
        raise pair_binary.computation_error(exc) from exc
    if evidence["input_digest"] != input_digest or evidence["policy_digest"] != policy_digest:
        raise EvidenceError("INTERNAL_ERROR", "DIGEST_DISAGREEMENT")
    validate(
        packaged_validator("paired_binary_evidence.v1"),
        evidence,
        EvidenceError,
        "INTERNAL_ERROR",
    )
    return review


def main(argv: list[str]) -> int:
    return run_cli("review-pair", argv, configure, produce, "paired_review.v1")
