"""Explicit process-wiring stub, NOT a Goppi closure verifier or native evidence.

This stdlib-only script also runs outside the checkout in installed CLI tests.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def verifier_response(snapshot: Path) -> dict[str, Any]:
    """Build consumed protocol fields without validating source content closure."""
    paired = json.loads((snapshot / "paired/manifest.json").read_bytes())
    trajectory_manifest = json.loads((snapshot / "trajectory/manifest.json").read_bytes())
    trajectory = json.loads((snapshot / "trajectory/trajectory.json").read_bytes())
    execution = json.loads((snapshot / "paired/execution.json").read_bytes())
    return {
        "schema": "goppi.command_result.v1",
        "command": "run",
        "status": "success",
        "code": "COMMAND_COMPLETED",
        "exit_code": 0,
        "human_review": "required",
        "merge_authorized": False,
        "revert_authorized": False,
        "data": {
            "schema": "goppi.paired_run_verification_result.v2",
            "status": "valid",
            "layout": "paired_trajectory_handoff",
            "input": str(snapshot),
            "paired_output": str(snapshot / "paired"),
            "format": "v1",
            "format_scope": "terminal_native_paired_run",
            "cycle_status": execution["source"]["cycle_status"],
            "row_count": paired["row_count"],
            "manifest_digest": paired["content_digest"],
            "trajectory": {
                "status": "valid",
                "output": str(snapshot / "trajectory"),
                "format": "v1",
                "manifest_digest": trajectory_manifest["content_digest"],
                "availability_counts": trajectory["availability_counts"],
                "capture_profile": trajectory["profile"],
            },
            "integrity_scope": "self_consistent_content_closure",
            "authenticity": "content_digest_only",
            "local_checks": {
                "source_authentication": "not_reperformed",
                "retained_trace_binding": "not_reperformed",
            },
            "portable_checks": {"content_closure": "valid"},
            "source_local_authentication_independently_reverified": False,
            "producer_projection_independently_verified": False,
            "independent_provenance_established": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("operation", choices=["verify-paired"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--json", action="store_true", required=True)
    args = parser.parse_args()
    print(json.dumps(verifier_response(args.input)))


if __name__ == "__main__":
    main()
