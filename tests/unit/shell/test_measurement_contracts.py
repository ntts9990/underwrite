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


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / f"{name}.schema.json").read_text())


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


def test_current_event_contract_assets_and_policy_required_fields() -> None:
    from underwrite.cli.measure import measurement_validators

    current = ("measurement_semantics.v1", "measurement_policy.v3", "read.v2")
    for name in current:
        Draft202012Validator.check_schema(_schema(name))
        assert (ROOT / "src/underwrite/_contracts" / f"{name}.schema.json").read_bytes() == (
            ROOT / "contracts" / f"{name}.schema.json"
        ).read_bytes()
    _, validator = measurement_validators()
    policy = json.loads((ROOT / "fixtures/examples/event-measurement/policy.json").read_bytes())
    validator.validate(policy)
    for field in policy:
        with pytest.raises(ValidationError):
            validator.validate({key: value for key, value in policy.items() if key != field})
    with pytest.raises(ValidationError):
        validator.validate(
            json.loads((ROOT / "fixtures/examples/measurement/policy.json").read_bytes())
        )


def test_current_event_policy_nested_objects_are_closed() -> None:
    from underwrite.cli.measure import measurement_validators

    _, validator = measurement_validators()
    original = json.loads((ROOT / "fixtures/examples/event-measurement/policy.json").read_bytes())
    for name in ("subject", "eprocess", "assumptions", "diagnostics"):
        policy = copy.deepcopy(original)
        policy[name]["undeclared"] = True
        with pytest.raises(ValidationError):
            validator.validate(policy)
        for field in original[name]:
            policy = copy.deepcopy(original)
            del policy[name][field]
            with pytest.raises(ValidationError):
                validator.validate(policy)
