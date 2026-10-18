"""The Promptfoo row view stays closed and derived from accounting's contract."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from underwrite.cli import capabilities, main
from underwrite.instrument.evidence.accounting import audit_promptfoo_with_rows
from underwrite.instrument.ingest.accounting import audit_promptfoo_row_index_file

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/promptfoo_row_index.v2.schema.json"
SAMPLE = ROOT / "fixtures/golden/external/promptfoo-0.124.1/results.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA.read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_generated_and_packaged_contract_match() -> None:
    checked = subprocess.run(
        [sys.executable, "scripts/build_promptfoo_compat.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    packaged = files("underwrite").joinpath("_contracts", SCHEMA.name)
    assert packaged.read_bytes() == SCHEMA.read_bytes()


def test_contract_accepts_real_view_and_rejects_private_fields_and_false_classes() -> None:
    validator = _validator()
    report = audit_promptfoo_row_index_file(SAMPLE)
    validator.validate(report)  # pyright: ignore[reportUnknownMemberType]
    for section, field, value in (
        ("rows", "prompt", "private"),
        ("rows", "score", 0),
        ("rows", "category", "errors"),
        ("rows", "source_failure_reason", 3),
        ("source", "claims_verified", True),
        ("scope", "planned_evaluations", "complete"),
    ):
        invalid = cast(dict[str, Any], deepcopy(report))
        target = invalid[section][0] if section == "rows" else invalid[section]
        target[field] = value
        with pytest.raises(ValidationError):
            validator.validate(invalid)  # pyright: ignore[reportUnknownMemberType]


def test_capabilities_describe_only_current_row_index_route() -> None:
    report = capabilities.report(main.COMMAND_DESCRIPTORS)
    commands = cast(list[dict[str, Any]], report["commands"])
    command = next(item for item in commands if item["command"] == "audit-promptfoo")
    profiles = cast(list[dict[str, Any]], command["profiles"])
    row_profiles = [item for item in profiles if item["name"].endswith("|row-index")]
    assert [item["name"] for item in row_profiles] == [
        "promptfoo.eval-output|auto|row-index",
    ]
    assert row_profiles[0]["required_inputs"] == ["file", "view"]
    assert row_profiles[0]["output_schemas"] == ["promptfoo_row_index.v2"]


@pytest.mark.parametrize(
    "success,reason,category",
    [
        (True, 0, "successes"),
        (True, 1, "successes"),
        (True, 2, "successes"),
        (False, 0, "failures"),
        (False, 1, "failures"),
        (False, 2, "errors"),
    ],
)
def test_schema_category_rule_matches_pure_classifier_for_every_valid_flag_pair(
    success: bool, reason: int, category: str
) -> None:
    _, classified = audit_promptfoo_with_rows(
        {"successes": 0, "failures": 0, "errors": 0},
        [{"success": success, "failureReason": reason}],
    )
    assert classified[0]["category"] == category
    schema = cast(dict[str, Any], _validator().schema)
    validator = Draft202012Validator(schema["properties"]["rows"]["items"])
    validator.validate(classified[0])  # pyright: ignore[reportUnknownMemberType]
    wrong = {**classified[0], "category": "errors" if category != "errors" else "successes"}
    with pytest.raises(ValidationError):
        validator.validate(wrong)  # pyright: ignore[reportUnknownMemberType]
