"""Aggregate outcomes cannot reconstruct the paired distribution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _repo_paths import repo_root

from underwrite.instrument.evidence.paired_binary import evaluate_paired_binary

EXAMPLES = repo_root(Path(__file__).resolve()) / "fixtures/examples/evidence"


def _arm_counts(document: dict[str, Any]) -> dict[str, list[int]]:
    return {
        arm: sorted(row["outcome"] for row in document["slot_records"] if row["arm"] == arm)
        for arm in ("baseline", "candidate")
    }


def test_equal_arm_totals_do_not_determine_paired_interval() -> None:
    aligned = json.loads((EXAMPLES / "synthetic-summary-aligned.json").read_bytes())
    crossed = json.loads((EXAMPLES / "synthetic-summary-crossed.json").read_bytes())
    policy = json.loads((EXAMPLES / "paired-binary-policy.json").read_bytes())
    assert _arm_counts(aligned) == _arm_counts(crossed)
    left = evaluate_paired_binary(aligned, policy)
    right = evaluate_paired_binary(crossed, policy)
    assert left["status"] == right["status"] == "measured"
    assert left["accounting"] == right["accounting"]
    left_estimate: Any = left["estimate"]
    right_estimate: Any = right["estimate"]
    assert left_estimate["n"] == right_estimate["n"]
    assert left_estimate["effect"] == right_estimate["effect"]
    assert left_estimate["interval"] != right_estimate["interval"]
