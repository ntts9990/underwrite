"""Compare two digest-bound, explicitly supplied declaration sets."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite.cli.evidence import EvidenceError, read_validated, run_cli
from underwrite.instrument.evidence.declarations import DeclarationError, compare_declarations

AUDIT_MAX_BYTES = 1024 * 1024
AUDIT_MAX_DEPTH = 32
MANIFEST_MAX_BYTES = 64 * 1024
MANIFEST_MAX_DEPTH = 16
LIMITATIONS = [
    "DECLARATIONS_NOT_EXECUTION_EVIDENCE",
    "DIGEST_BINDING_NOT_PROVENANCE",
    "SAME_NOT_SEMANTIC_EQUIVALENCE",
    "NO_MEASUREMENT_OR_APPROVAL",
]


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--baseline", required=True, type=Path, help="Local promptfoo_accounting.v1 JSON."
    )
    parser.add_argument(
        "--candidate", required=True, type=Path, help="Local promptfoo_accounting.v1 JSON."
    )
    parser.add_argument(
        "--manifest", required=True, type=Path, help="Local declared_comparison_case.v1 JSON."
    )
    parser.add_argument("--json", action="store_true", help="Canonical report on stdout.")
    parser.description = (
        "Compare only declarations supplied for two exact Promptfoo accounting reports. "
        "Matching declarations do not verify runtime conditions or score equivalence. "
        "Exit 0 means a report was produced, not measurement or approval."
    )


def _side(value: object) -> dict[str, Any]:
    # The installed manifest contract validates this shape before it reaches here.
    return cast(dict[str, Any], value)


def produce(args: argparse.Namespace) -> dict[str, object]:
    baseline, baseline_digest = read_validated(
        cast(Path, args.baseline),
        "promptfoo_accounting.v1",
        "/baseline",
        AUDIT_MAX_BYTES,
        AUDIT_MAX_DEPTH,
    )
    candidate, candidate_digest = read_validated(
        cast(Path, args.candidate),
        "promptfoo_accounting.v1",
        "/candidate",
        AUDIT_MAX_BYTES,
        AUDIT_MAX_DEPTH,
    )
    manifest, manifest_digest = read_validated(
        cast(Path, args.manifest),
        "declared_comparison_case.v1",
        "/manifest",
        MANIFEST_MAX_BYTES,
        MANIFEST_MAX_DEPTH,
    )
    left, right = _side(manifest["baseline"]), _side(manifest["candidate"])
    for side, declared, actual in (
        ("baseline", left, baseline_digest),
        ("candidate", right, candidate_digest),
    ):
        if declared["audit_digest"] != actual:
            raise EvidenceError(
                "INPUT_BINDING_MISMATCH", "AUDIT_DIGEST_MISMATCH", f"/manifest/{side}/audit_digest"
            )
        if not cast(str, declared["run_ref"]).strip():
            raise EvidenceError("INVALID_INPUT", "INVALID_RUN_REF", f"/manifest/{side}/run_ref")
    try:
        comparison = compare_declarations(
            left["conditions"], right["conditions"], left["metric"], right["metric"]
        )
    except DeclarationError as exc:
        raise EvidenceError("INVALID_INPUT", str(exc), "/manifest") from exc
    return {
        "schema": "declared_comparison.v1",
        "input_digests": {"baseline": baseline_digest, "candidate": candidate_digest},
        "manifest_digest": manifest_digest,
        "audit_relations": {
            "baseline": baseline["declared_relation"],
            "candidate": candidate["declared_relation"],
        },
        "declared_relation": comparison["declared_relation"],
        "actual_conditions_verified": False,
        "semantic_equivalence_verified": False,
        "differences": comparison["differences"],
        "unknowns": comparison["unknowns"],
        "limitations": list(LIMITATIONS),
    }


def main(argv: list[str]) -> int:
    return run_cli(
        "compare-declarations",
        argv,
        configure,
        produce,
        "declared_comparison.v1",
    )
