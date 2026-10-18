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
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_SCHEMA,
    account_deepeval_compatible,
)

ROOT = Path(__file__).resolve().parents[2]


def test_generated_contract_is_current_and_valid() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_deepeval_compat.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    Draft202012Validator.check_schema(
        json.loads((ROOT / f"contracts/{ACCOUNTING_SCHEMA}.schema.json").read_bytes())
    )


def test_state_branches_prevent_score_error_and_absence_conflation() -> None:
    validator = cast(
        Validator,
        Draft202012Validator(
            json.loads((ROOT / "contracts/deepeval_accounting.v3.schema.json").read_bytes())
        ),
    )
    base = {"name": "example", "threshold": 0.5, "success": False}
    result: dict[str, Any] = {
        "schema": ACCOUNTING_SCHEMA,
        "raw_hash": "sha256:" + "a" * 64,
        **account_deepeval_compatible(
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


def test_current_flaky_branch_preserves_absent_verdict_and_source_flaky() -> None:
    validator = cast(
        Validator,
        Draft202012Validator(
            json.loads((ROOT / "contracts/deepeval_accounting.v3.schema.json").read_bytes())
        ),
    )
    result: dict[str, Any] = {
        "schema": ACCOUNTING_SCHEMA,
        "raw_hash": "sha256:" + "a" * 64,
        **account_deepeval_compatible(
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
