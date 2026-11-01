"""Closed installed result schema and generated contract agreement."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import ValidationError

from underwrite.cli.local_json import Validator
from underwrite.instrument.evidence.goppi_trajectory import (
    MEMBERS,
    OUTCOME_ACTIONS_SCHEMA_V2,
    RESULT_SCHEMA,
    RESULT_SCHEMA_V2,
    analyze_goppi_trajectory,
)
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/conformance/goppi-trajectory/exchange"


@pytest.fixture
def report() -> dict[str, Any]:
    return analyze_goppi_trajectory(
        json.loads((FIXTURE / "paired/execution.json").read_bytes()),
        tuple(
            json.loads(line) for line in (FIXTURE / "paired/rows.jsonl").read_text().splitlines()
        ),
        json.loads((FIXTURE / "trajectory/trajectory.json").read_bytes()),
        {
            p: (
                "sha256:" + hashlib.sha256((FIXTURE / p).read_bytes()).hexdigest(),
                (FIXTURE / p).stat().st_size,
            )
            for p in MEMBERS
        },
    )


def test_closed_schema_accepts_direct_pure_and_admitted_result(report: dict[str, Any]) -> None:
    validator = cast(Validator, packaged_validator(RESULT_SCHEMA))
    validator.validate(report)
    report["trust_scope"].update(
        native_verification="local_goppi_same_byte_snapshot",
        integrity_scope="self_consistent_content_closure",
        authenticity="content_digest_only",
    )
    validator.validate(report)
    report["trust_scope"]["native_verification"] = "not_performed"
    with pytest.raises(ValidationError):
        validator.validate(report)


@pytest.mark.parametrize(
    "mutation",
    ["extra", "authority", "unavailable_zero", "false_number", "process_extra", "task_command"],
)
def test_schema_refuses_semantic_masquerades(report: dict[str, Any], mutation: str) -> None:
    first = report["comparisons"][0]
    if mutation == "extra":
        report["raw_trace"] = "not allowed"
    elif mutation == "authority":
        report["authority"]["merge_authorized"] = True
    elif mutation == "unavailable_zero":
        first["baseline"].update(
            source_availability="not_retained",
            capture_completeness="incomplete",
            unavailable_reason="capture_policy_unrecorded",
            llm_span_count=0,
            tool_status_counts=[],
        )
    elif mutation == "false_number":
        first["baseline"]["outcome"]["scored_value"] = 0
    elif mutation == "process_extra":
        first["baseline"]["verifier_process"] = {
            "state": "unlinked",
            "receipt_identity_digest": "sha256:" + "a" * 64,
        }
    else:
        report["next_verification_tasks"][0]["command"] = "execute"
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(RESULT_SCHEMA)).validate(report)


def test_schema_is_generated_from_policy_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    name = "build_goppi_trajectory_contract"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert module.build() == json.loads(
        (ROOT / "contracts" / f"{RESULT_SCHEMA}.schema.json").read_bytes()
    )
    assert module.build(version=2) == json.loads(
        (ROOT / "contracts" / f"{RESULT_SCHEMA_V2}.schema.json").read_bytes()
    )
    assert module.build(outcome_actions=True, version=2) == json.loads(
        (ROOT / "contracts" / f"{OUTCOME_ACTIONS_SCHEMA_V2}.schema.json").read_bytes()
    )


def test_goppi_trajectory_contracts_are_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/build_goppi_trajectory_contract.py", "--check"],
        cwd=ROOT,
        check=True,
    )


def test_context_missing_and_unavailable_outcome_variants(report: dict[str, Any]) -> None:
    validator = cast(Validator, packaged_validator(RESULT_SCHEMA))
    for outcome in (
        {
            "status": "endpoint_failure",
            "reason": "agent_timeout",
            "scored_value": False,
            "verifier_value": None,
        },
        {
            "status": "missing",
            "reason": "not_started",
            "scored_value": None,
            "verifier_value": None,
        },
        {"status": "observed", "value_kind": "scalar", "scored_value": 0.5, "verifier_value": 0.5},
    ):
        variant = copy.deepcopy(report)
        variant["comparisons"][0]["baseline"]["outcome"] = outcome
        validator.validate(variant)


def test_v1_schema_bytes_remain_pinned() -> None:
    raw = (ROOT / "contracts" / f"{RESULT_SCHEMA}.schema.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "946bbf749acb448899bca0a5438ffc67c69c835a702cd60d38d79c9ae8a80775"
    )
    actions_raw = (
        ROOT / "contracts" / "goppi_trajectory_outcome_actions.v1.schema.json"
    ).read_bytes()
    assert hashlib.sha256(actions_raw).hexdigest() == (
        "c3ff3787340fc4cc2c8fb1c6709831375431ff1acb3f70ff10618cf50469af5c"
    )


def _v2_report(
    report: dict[str, Any],
    *,
    outcome_actions: bool = False,
    accepted_source: str | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(report)
    result["schema"] = OUTCOME_ACTIONS_SCHEMA_V2 if outcome_actions else RESULT_SCHEMA_V2
    binding = result["trust_scope"]["outcome_harness_binding"]
    if accepted_source is None:
        result["trust_scope"] = {
            "verifier_report_state": "not_performed",
            "verifier_source": None,
            "verifier_identity_verified": False,
            "response_snapshot_binding": "not_checked",
            "snapshot_postcheck": "not_checked",
            "content_closure": "not_checked",
            "authenticity": "not_checked",
            "source_authentication_independently_verified": False,
            "producer_projection_independently_verified": False,
            "outcome_harness_binding": binding,
        }
    else:
        result["trust_scope"] = {
            "verifier_report_state": "report_accepted",
            "verifier_source": accepted_source,
            "verifier_identity_verified": False,
            "response_snapshot_binding": "selected_fields_matched",
            "snapshot_postcheck": "unchanged",
            "content_closure": "verifier_reported_valid",
            "authenticity": "verifier_reported_content_digest_only",
            "source_authentication_independently_verified": False,
            "producer_projection_independently_verified": False,
            "outcome_harness_binding": binding,
        }
    return result


@pytest.mark.parametrize(
    "accepted_source",
    [None, "caller_supplied_python_port", "operator_configured_executable"],
)
def test_v2_review_schema_accepts_only_declared_trust_states(
    report: dict[str, Any], accepted_source: str | None
) -> None:
    cast(Validator, packaged_validator(RESULT_SCHEMA_V2)).validate(
        _v2_report(report, accepted_source=accepted_source)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verifier_source", None),
        ("verifier_identity_verified", True),
        ("response_snapshot_binding", "not_checked"),
        ("snapshot_postcheck", "not_checked"),
        ("content_closure", "not_checked"),
        ("authenticity", "not_checked"),
        ("source_authentication_independently_verified", True),
        ("producer_projection_independently_verified", True),
        ("native_verification", "local_goppi_same_byte_snapshot"),
        ("integrity_scope", "self_consistent_content_closure"),
    ],
)
def test_v2_review_schema_rejects_partial_or_elevated_accepted_trust(
    report: dict[str, Any], field: str, value: object
) -> None:
    result = _v2_report(report, accepted_source="caller_supplied_python_port")
    result["trust_scope"][field] = value
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(RESULT_SCHEMA_V2)).validate(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verifier_source", "operator_configured_executable"),
        ("verifier_identity_verified", True),
        ("response_snapshot_binding", "selected_fields_matched"),
        ("snapshot_postcheck", "unchanged"),
        ("content_closure", "verifier_reported_valid"),
        ("authenticity", "verifier_reported_content_digest_only"),
    ],
)
def test_v2_review_schema_rejects_claims_when_report_not_performed(
    report: dict[str, Any], field: str, value: object
) -> None:
    result = _v2_report(report)
    result["trust_scope"][field] = value
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(RESULT_SCHEMA_V2)).validate(result)


@pytest.fixture
def action_report(report: dict[str, Any]) -> dict[str, Any]:
    from underwrite.instrument.evidence.goppi_trajectory import analyze_goppi_trajectory_actions

    return analyze_goppi_trajectory_actions(
        json.loads((FIXTURE / "paired/execution.json").read_bytes()),
        tuple(
            json.loads(line) for line in (FIXTURE / "paired/rows.jsonl").read_text().splitlines()
        ),
        json.loads((FIXTURE / "trajectory/trajectory.json").read_bytes()),
        {item["path"]: (item["sha256"], item["bytes"]) for item in report["input_members"]},
    )


def test_outcome_actions_schema_accepts_pure_result_and_matches_packaged_asset(
    action_report: dict[str, Any],
) -> None:
    from underwrite.instrument.evidence.goppi_trajectory import OUTCOME_ACTIONS_SCHEMA

    authored = (ROOT / "contracts" / f"{OUTCOME_ACTIONS_SCHEMA}.schema.json").read_bytes()
    packaged = (
        ROOT / "src/underwrite/_contracts" / f"{OUTCOME_ACTIONS_SCHEMA}.schema.json"
    ).read_bytes()
    assert authored == packaged
    validator = cast(Validator, packaged_validator(OUTCOME_ACTIONS_SCHEMA))
    validator.validate(action_report)
    action_report["trust_scope"].update(
        native_verification="local_goppi_same_byte_snapshot",
        integrity_scope="self_consistent_content_closure",
        authenticity="content_digest_only",
    )
    validator.validate(action_report)


@pytest.mark.parametrize(
    "accepted_source",
    [None, "caller_supplied_python_port", "operator_configured_executable"],
)
def test_v2_outcome_actions_schema_accepts_declared_trust_states(
    action_report: dict[str, Any], accepted_source: str | None
) -> None:
    authored = (ROOT / "contracts" / f"{OUTCOME_ACTIONS_SCHEMA_V2}.schema.json").read_bytes()
    packaged = (
        ROOT / "src/underwrite/_contracts" / f"{OUTCOME_ACTIONS_SCHEMA_V2}.schema.json"
    ).read_bytes()
    assert authored == packaged
    cast(Validator, packaged_validator(OUTCOME_ACTIONS_SCHEMA_V2)).validate(
        _v2_report(
            action_report,
            outcome_actions=True,
            accepted_source=accepted_source,
        )
    )


@pytest.mark.parametrize(
    "mutation",
    ["owner", "command", "authorization", "scope_size", "kind", "extra"],
)
def test_outcome_actions_schema_refuses_task_masquerades(
    action_report: dict[str, Any], mutation: str
) -> None:
    from underwrite.instrument.evidence.goppi_trajectory import OUTCOME_ACTIONS_SCHEMA

    task = next(
        item for item in action_report["next_verification_tasks"]
        if item["kind"] == "inspect_outcome_change_without_tool_status_change"
    )
    if mutation == "owner":
        task["owner"] = "goppi"
    elif mutation == "command":
        task["command"] = "rerun"
    elif mutation == "authorization":
        task["execution_authorized"] = True
    elif mutation == "scope_size":
        task["scope"] = task["scope"] * 157
    elif mutation == "kind":
        task["kind"] = "approve_change"
    else:
        action_report["fabricated"] = True
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(OUTCOME_ACTIONS_SCHEMA)).validate(action_report)
