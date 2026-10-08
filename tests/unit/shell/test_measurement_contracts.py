"""Standalone behavior and boundary checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator, ValidationError

ROOT = repo_root(Path(__file__).resolve())
ASSETS = ("read.v1", "measurement_policy.v2", "statistical_seed.v1", "measurement_error.v1")


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / f"{name}.schema.json").read_text())


def _policy_validator() -> Validator:
    # Trusted test assets only; no URI resolver or retrieval.
    schema = _schema("measurement_policy.v2")
    assert schema["properties"]["subject"] == {
        "$ref": "urn:underwrite:contract:read.v1#/$defs/subject"
    }
    assert schema["properties"]["seed"] == {"$ref": "urn:underwrite:contract:statistical_seed.v1"}
    schema["properties"]["subject"] = copy.deepcopy(_schema("read.v1")["$defs"]["subject"])
    schema["properties"]["seed"] = {
        k: v for k, v in _schema("statistical_seed.v1").items() if not k.startswith("$")
    }
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _policy() -> dict[str, Any]:
    return json.loads((ROOT / "fixtures/examples/measurement/policy.json").read_text())


def test_captured_policies_validate_without_inferred_parameters() -> None:
    validator = _policy_validator()
    for path in (ROOT / "fixtures/examples/measurement").glob("policy*.json"):
        validator.validate(json.loads(path.read_text()))
    policy = _policy()
    for field in policy:
        incomplete = {k: v for k, v in policy.items() if k != field}
        with pytest.raises(ValidationError):
            validator.validate(incomplete)


@pytest.mark.parametrize(
    "field,value",
    [
        ("required_instruments", []),
        ("quorum", 0),
        ("seed", None),
        ("subject", {"subject_id": "invalid identity", "conditions": {}}),
        ("fusion", {"combine": "product", "independence": None}),
        ("stratification", {"mode": "none", "axes": ["split"], "requested": []}),
        ("stratification", {"mode": "split", "axes": [], "requested": []}),
    ],
)
def test_policy_rejects_incomplete_or_unsupported_shape(field: str, value: object) -> None:
    policy = _policy()
    policy[field] = value
    with pytest.raises(ValidationError):
        _policy_validator().validate(policy)


def test_measurement_assets_are_packaged_contracts() -> None:
    """Their link and wheel/sdist bytes are tests/gates/test_packaged_contracts.py's gate."""
    packaged = {path.name for path in (ROOT / "src/underwrite/_contracts").iterdir()}
    assert {f"{name}.schema.json" for name in ASSETS} <= packaged


def test_each_nested_policy_object_is_closed_and_complete() -> None:
    validator = _policy_validator()
    for name in (
        "inputs",
        "subject",
        "eprocess",
        "fusion",
        "calibration",
        "panel",
        "stratification",
    ):
        policy = _policy()
        policy[name]["undeclared"] = True
        with pytest.raises(ValidationError):
            validator.validate(policy)
        for field in _policy()[name]:
            policy = _policy()
            del policy[name][field]
            with pytest.raises(ValidationError):
                validator.validate(policy)


def test_split_mode_uses_exact_requested_keys() -> None:
    policy = _policy()
    policy["stratification"] = {
        "mode": "split",
        "axes": ["split"],
        "requested": [{"split": "unobserved"}],
    }
    _policy_validator().validate(policy)
    policy["stratification"]["requested"] = [{"locale": "ko"}]
    with pytest.raises(ValidationError):
        _policy_validator().validate(policy)


def test_error_contract_is_finite_and_never_echoes_arbitrary_diagnostics() -> None:
    schema = _schema("measurement_error.v1")
    Draft202012Validator.check_schema(schema)
    validator = cast("Validator", Draft202012Validator(schema))
    error = {
        "schema": "measurement_error.v1",
        "code": "INVALID_POLICY",
        "reason": "INVALID_POLICY",
        "location": "/policy",
        "next_action": "PROVIDE_COMPLETE_POLICY",
        "retryable": False,
        "exit_code": 2,
    }
    validator.validate(error)
    for field in ("code", "reason", "location", "next_action"):
        with pytest.raises(ValidationError):
            validator.validate({**error, field: "raw private input"})
    with pytest.raises(ValidationError):
        validator.validate({**error, "payload": "private"})
