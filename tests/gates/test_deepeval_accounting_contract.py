"""Rule-derived schema preserves score/error/absence distinctions."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from underwrite.cli.local_json import Validator
from underwrite.instrument.evidence.deepeval_accounting import RULE_PROFILE_V2, account_deepeval
from underwrite.instrument.ingest.deepeval_accounting import SOURCE

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("version,suffix", [("v1", ""), ("v2", "_v2")])
def test_generated_contract_is_current_and_valid(version: str, suffix: str) -> None:
    result = subprocess.run(
        [sys.executable, f"scripts/build_deepeval_accounting{suffix}.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    Draft202012Validator.check_schema(
        json.loads((ROOT / f"contracts/deepeval_accounting.{version}.schema.json").read_bytes())
    )


def test_state_branches_prevent_score_error_and_absence_conflation() -> None:
    validator = cast(
        Validator,
        Draft202012Validator(
            json.loads((ROOT / "contracts/deepeval_accounting.v1.schema.json").read_bytes())
        ),
    )
    base = {"name": "example", "threshold": 0.5, "success": False}
    result: dict[str, Any] = {
        "schema": "deepeval_accounting.v1",
        "source": {"format": SOURCE[0], "format_version": SOURCE[1], "claims_verified": False},
        "raw_hash": "sha256:" + "a" * 64,
        "observation_digest": "sha256:" + "b" * 64,
        **account_deepeval(
            {
                "testCases": [
                    {
                        "name": "case",
                        "metricsData": [
                            {**base, "score": 0},
                            {**base, "error": "example error"},
                            base,
                        ],
                    }
                ]
            }
        ),
    }
    validator.validate(result)
    for index, patch in (
        (0, {"score": False}),
        (1, {"score": 0}),
        (2, {"score": None}),
        (0, {"applied_rule": "ERROR_PRESENT"}),
    ):
        invalid = deepcopy(result)
        invalid["cases"][0]["metrics"][index].update(patch)
        with pytest.raises(ValidationError):
            validator.validate(invalid)


def test_v2_contract_preserves_absent_verdict_and_source_flaky() -> None:
    validator = cast(
        Validator,
        Draft202012Validator(
            json.loads((ROOT / "contracts/deepeval_accounting.v2.schema.json").read_bytes())
        ),
    )
    result: dict[str, Any] = {
        "schema": "deepeval_accounting.v2",
        "source": {"format": SOURCE[0], "format_version": "4.2.8", "claims_verified": False},
        "raw_hash": "sha256:" + "a" * 64,
        "observation_digest": "sha256:" + "b" * 64,
        **account_deepeval(
            {
                "testCases": [
                    {
                        "name": "case",
                        "flaky": True,
                        "success": False,
                        "metricsData": [
                            {"name": "score only", "flaky": False, "score": 0.3},
                            {"name": "error", "flaky": True, "error": "example"},
                            {"name": "unscored", "flaky": False},
                        ],
                    }
                ]
            },
            rule_profile=RULE_PROFILE_V2,
        ),
    }
    validator.validate(result)
    assert result["cases"][0]["metrics"][0]["source_success"] is None
    assert result["cases"][0]["metrics"][0]["threshold"] is None
    assert result["counts"]["metrics"]["scored_source_success_absent"] == 1
    for index, patch in (
        (0, {"source_success": 0}),
        (0, {"threshold": False}),
        (0, {"source_flaky": None}),
        (0, {"error": "unexpected"}),
        (1, {"score": 0}),
        (2, {"score": None}),
    ):
        invalid = deepcopy(result)
        invalid["cases"][0]["metrics"][index].update(patch)
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    for path in (
        ("cases", 0, "source_flaky"),
        ("cases", 0, "metrics", 0, "source_flaky"),
        ("cases", 0, "metrics", 0, "source_success"),
        ("cases", 0, "metrics", 0, "threshold"),
        ("counts", "metrics", "scored_source_success_absent"),
        ("counts", "metrics", "source_flaky_true"),
        ("counts", "cases", "source_flaky_true"),
    ):
        invalid = deepcopy(result)
        parent: Any = invalid
        for key in path[:-1]:
            parent = parent[key]
        del parent[path[-1]]
        with pytest.raises(ValidationError):
            validator.validate(invalid)
