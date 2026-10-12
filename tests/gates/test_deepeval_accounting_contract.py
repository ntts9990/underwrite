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
from underwrite.instrument.evidence.deepeval_accounting import account_deepeval
from underwrite.instrument.ingest.deepeval_accounting import SOURCE

ROOT = Path(__file__).resolve().parents[2]


def test_generated_contract_is_current_and_valid() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_deepeval_accounting.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    Draft202012Validator.check_schema(
        json.loads((ROOT / "contracts/deepeval_accounting.v1.schema.json").read_bytes())
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
