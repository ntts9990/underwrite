"""Pinned synthetic C55 projection against Underwrite's installed common result."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.cli.inspect_result import inspect_result_file
from underwrite.cli.local_json import Validator
from underwrite.instrument.evidence.common_result import check_result_invariants
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/conformance/goppi-c55-common"
MEMBERS = (
    "assessment.json",
    "evidence-result.json",
    "schema.json",
    "ume1-schema.json",
    "manifest.json",
)
EXPECTED_CASES = (
    ("verifier_binary_true", "binary_true", "observed", True, None, "none"),
    ("verifier_binary_false", "binary_false", "observed", False, None, "none"),
    (
        "verifier_unknown",
        "verifier_unknown",
        "abstained",
        None,
        "SOURCE_ABSTAINED",
        "inspect_source",
    ),
    (
        "verifier_indeterminate",
        "verifier_indeterminate",
        "abstained",
        None,
        "SOURCE_ABSTAINED",
        "inspect_source",
    ),
    ("not_provided", "not_provided", "missing", None, "SOURCE_MISSING", "inspect_source"),
    (
        "operational_failure",
        "verifier_exit_error",
        "error",
        None,
        "SOURCE_ERROR",
        "inspect_source",
    ),
    (
        "not_started",
        "not_started_after_operational_failure",
        "missing",
        None,
        "SOURCE_MISSING",
        "inspect_source",
    ),
)


def _document(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURE / name).read_bytes()))


def _identity_matches(name: str, raw: bytes) -> bool:
    pin = _document("pins.json")["members"][name]
    return len(raw) == pin["bytes"] and digest_bytes(raw) == pin["sha256"]


def _validate_common_result(result: dict[str, Any]) -> None:
    validator = cast(Validator, packaged_validator("evidence_result.v1"))
    validator.validate(result)
    check_result_invariants(result)


def _assert_c55_correspondence(assessment: dict[str, Any], result: dict[str, Any]) -> None:
    """Check this fixture's seven ordered source meanings, not a general C55 parser."""
    assert (result["source"]["format"], result["source"]["format_version"]) == (
        "goppi.operator_answer_assessment",
        "v1",
    )
    cases, items = assessment["cases"], result["items"]
    assert len(cases) == len(items) == len(EXPECTED_CASES)
    for index, (case, item, expected) in enumerate(zip(cases, items, EXPECTED_CASES, strict=True)):
        disposition, reason_code, availability, value, reason, action = expected
        assert (case["case_id"], case["disposition"], case["reason_code"]) == (
            f"synthetic-case-{index + 1:02d}",
            disposition,
            reason_code,
        )
        assert item["coordinate"] == {"kind": "case", "index": index}
        assert item["applicability"] == "applicable"
        assert item["method"] is None
        assert item["policy"] is None
        assert type(item["value"]) is type(value)
        assert (item["availability"], item["value"], item["reason"], item["next_action"]) == (
            availability,
            value,
            reason,
            action,
        )
        assert item["metric"] == {"semantic_binding": "unbound", "source_metric_index": 0}
        assert (item["origin"], item["permitted_effect"]) == ("source_claim", "descriptive")


def test_pinned_public_members_have_exact_bytes() -> None:
    pins = _document("pins.json")
    assert pins["schema"] == "underwrite.goppi_c55_common_fixture_pins.v1"
    assert type(pins["source_commit"]) is str
    assert re.fullmatch(r"[0-9a-f]{40}", pins["source_commit"]) is not None
    assert pins["source_directory"] == "fixtures/conformance/operator-answer-exchange"
    assert tuple(pins["members"]) == MEMBERS
    for name in MEMBERS:
        assert _identity_matches(name, (FIXTURE / name).read_bytes()), name


def test_selected_result_obeys_installed_contract_and_c55_source_meanings() -> None:
    assessment = _document("assessment.json")
    result = _document("evidence-result.json")
    _validate_common_result(result)
    inspected = inspect_result_file(FIXTURE / "evidence-result.json")
    assert inspected["selected_result"] == {
        "format": "evidence_result.v1",
        "raw_hash": digest_bytes((FIXTURE / "evidence-result.json").read_bytes()),
    }
    expected_counts = {
        state: sum(expected[2] == state for expected in EXPECTED_CASES)
        for state in ("observed", "missing", "error", "abstained")
    }
    assert inspected["availability_counts"] == {
        **expected_counts,
        "total": len(EXPECTED_CASES),
    }
    assert (FIXTURE / "ume1-schema.json").read_bytes() == (
        ROOT / "contracts/evidence_result.v1.schema.json"
    ).read_bytes()
    assert result["source"]["identity"] == {
        "kind": "single_file",
        "raw_hash": digest_bytes((FIXTURE / "assessment.json").read_bytes()),
    }
    assert result["population"] == {"unit": "case", "observed_count": 7, "planned_count": 7}
    assert result["processing"] == {"state": "analyzed", "reason": None, "next_action": "none"}
    assert result["verification"] == []
    assert assessment["producer_execution_authenticated"] is False
    assert assessment["control_expectations_authenticated"] is False
    assert assessment["merge_authorized"] is False
    assert assessment["human_review"] == "required"
    _assert_c55_correspondence(assessment, result)


def test_changed_member_bytes_break_pinned_identity() -> None:
    original = (FIXTURE / "evidence-result.json").read_bytes()
    assert _identity_matches("evidence-result.json", original)
    assert not _identity_matches("evidence-result.json", original + b" ")


def test_schema_invalid_item_is_rejected() -> None:
    changed = deepcopy(_document("evidence-result.json"))
    changed["items"][0]["availability"] = "invented"
    with pytest.raises(ValidationError):
        _validate_common_result(changed)


def test_schema_valid_unknown_to_missing_drift_needs_c55_oracle(tmp_path: Path) -> None:
    assessment = _document("assessment.json")
    changed = deepcopy(_document("evidence-result.json"))
    changed["items"][2].update(
        availability="missing", reason="SOURCE_MISSING", value=None, next_action="inspect_source"
    )
    _validate_common_result(changed)
    selected = tmp_path / "changed-result.json"
    selected.write_text(json.dumps(changed), encoding="utf-8")
    checks = cast(dict[str, str], inspect_result_file(selected)["checks"])
    assert checks["contract_shape"] == "valid"
    with pytest.raises(AssertionError):
        _assert_c55_correspondence(assessment, changed)


def test_schema_valid_boolean_to_numeric_drift_needs_c55_oracle(tmp_path: Path) -> None:
    assessment = _document("assessment.json")
    changed = deepcopy(_document("evidence-result.json"))
    changed["items"][0]["value"] = 1
    _validate_common_result(changed)
    selected = tmp_path / "changed-result.json"
    selected.write_text(json.dumps(changed), encoding="utf-8")
    checks = cast(dict[str, str], inspect_result_file(selected)["checks"])
    assert checks["contract_shape"] == "valid"
    with pytest.raises(AssertionError):
        _assert_c55_correspondence(assessment, changed)
