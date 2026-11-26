"""Closed installed result schema and generated contract agreement."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import ValidationError

from underwrite.cli.local_json import Validator
from underwrite.instrument.evidence.goppi_trajectory import (
    AVAILABILITIES,
    MEMBERS,
    OUTCOME_ACTIONS_SCHEMA_V3,
    RESULT_SCHEMA,
    RESULT_SCHEMA_V2,
    RESULT_SCHEMA_V3,
    RESULT_SCHEMA_V4,
    analyze_goppi_trajectory,
    review_goppi_trajectory,
)
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/conformance/goppi-trajectory/exchange"
FIXTURE_V2 = ROOT / "fixtures/conformance/goppi-execution-v2/paired-exchange-v2"


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
    for outcome_actions, schema in ((False, RESULT_SCHEMA_V3), (True, OUTCOME_ACTIONS_SCHEMA_V3)):
        assert module.build(outcome_actions=outcome_actions, version=3) == json.loads(
            (ROOT / "contracts" / f"{schema}.schema.json").read_bytes()
        )
    for outcome_actions, schema in (
        (False, RESULT_SCHEMA_V4),
        (True, "goppi_trajectory_outcome_actions.v4"),
    ):
        assert module.build(outcome_actions=outcome_actions, version=4) == json.loads(
            (ROOT / "contracts" / f"{schema}.schema.json").read_bytes()
        )
    assert module.build_verifier_response_v3() == json.loads(
        (ROOT / "contracts/goppi_trajectory_verifier_response.v3.schema.json").read_bytes()
    )
    # The direct-only v2 outcome-actions output has no remaining writer or reader.
    with pytest.raises(ValueError, match="UNSUPPORTED_GOPPI_TRAJECTORY_CONTRACT_VERSION"):
        module.build(outcome_actions=True, version=2)
    retired = "goppi_trajectory_outcome_actions.v2.schema.json"
    assert not os.path.lexists(ROOT / "contracts" / retired)
    assert not os.path.lexists(ROOT / "src/underwrite/_contracts" / retired)


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


def test_execution_v2_result_uses_v4_and_preserves_self_reported_limits() -> None:
    execution = json.loads((FIXTURE_V2 / "paired/execution.json").read_bytes())
    rows = tuple(
        json.loads(line) for line in (FIXTURE_V2 / "paired/rows.jsonl").read_text().splitlines()
    )
    trajectory = json.loads((FIXTURE_V2 / "trajectory/trajectory.json").read_bytes())
    identities = {
        path: (
            "sha256:" + hashlib.sha256((FIXTURE_V2 / path).read_bytes()).hexdigest(),
            (FIXTURE_V2 / path).stat().st_size,
        )
        for path in MEMBERS
    }
    result = cast(
        dict[str, Any], analyze_goppi_trajectory(execution, rows, trajectory, identities)
    )
    cast(Validator, packaged_validator(RESULT_SCHEMA_V4)).validate(result)
    assert result["schema"] == "goppi_trajectory_review.v4"
    assert result["producer_declarations"]["builds"]["export"]["dir_hash"] == (
        "4700ec2d5679e5454bf7eaa5eb59429cc0ff452e736f4828bbc91695c64df7d4"
    )
    assert result["producer_declarations"]["builds"]["independently_verified"] is False
    assert next(stage for stage in result["stages"] if stage["stage"] == "held_in")[
        "classification"
    ] == "indeterminate"
    not_run = next(stage for stage in result["stages"] if stage["stage"] == "held_out")
    assert not_run["execution_status"] == "not_run"
    assert not_run["stage_run_id"] is None
    assert not_run["verdict_state"] == "not_measured"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_intervention",
        "wrong_intervention_field",
        "unknown_intervention_state",
        "unrecognized_held_constant",
        "duplicate_held_constant",
    ],
)
def test_v4_schema_binds_intervention_and_held_constant_claims(mutation: str) -> None:
    execution = json.loads((FIXTURE_V2 / "paired/execution.json").read_bytes())
    rows = tuple(
        json.loads(line) for line in (FIXTURE_V2 / "paired/rows.jsonl").read_text().splitlines()
    )
    trajectory = json.loads((FIXTURE_V2 / "trajectory/trajectory.json").read_bytes())
    identities = {
        path: (
            "sha256:" + hashlib.sha256((FIXTURE_V2 / path).read_bytes()).hexdigest(),
            (FIXTURE_V2 / path).stat().st_size,
        )
        for path in MEMBERS
    }
    report = cast(
        dict[str, Any], analyze_goppi_trajectory(execution, rows, trajectory, identities)
    )
    validator = cast(Validator, packaged_validator(RESULT_SCHEMA_V4))
    comparison = report["comparisons"][0]["comparability"]
    if mutation == "missing_intervention":
        comparison.pop("intervention")
    elif mutation == "wrong_intervention_field":
        comparison["intervention"]["field"] = "context.runner_model"
    elif mutation == "unknown_intervention_state":
        comparison["intervention"]["state"] = "maybe"
    elif mutation == "unrecognized_held_constant":
        comparison["held_constant"].append("context.harness_identity_digest")
    else:
        comparison["held_constant"].append(comparison["held_constant"][0])
    with pytest.raises(ValidationError):
        validator.validate(report)


def test_execution_v2_unavailable_build_result_validates_against_v4_schema() -> None:
    execution = json.loads((FIXTURE_V2 / "paired/execution.json").read_bytes())
    rows = tuple(
        json.loads(line) for line in (FIXTURE_V2 / "paired/rows.jsonl").read_text().splitlines()
    )
    trajectory = json.loads((FIXTURE_V2 / "trajectory/trajectory.json").read_bytes())
    identities = {
        path: (
            "sha256:" + hashlib.sha256((FIXTURE_V2 / path).read_bytes()).hexdigest(),
            (FIXTURE_V2 / path).stat().st_size,
        )
        for path in MEMBERS
    }
    execution["export_build"].update(
        build_digest=None,
        build_digest_unavailable_reason="package_unverifiable",
    )
    execution["stages"][0]["stage_build"] = {
        "record_state": "not_recorded",
        "package_name": None,
        "package_version": None,
        "build_digest": None,
        "build_digest_unavailable_reason": None,
        "identity_assurance": None,
    }

    report = cast(
        dict[str, Any], analyze_goppi_trajectory(execution, rows, trajectory, identities)
    )
    cast(Validator, packaged_validator(RESULT_SCHEMA_V4)).validate(report)
    builds = report["producer_declarations"]["builds"]
    assert builds["export"]["state"] == "not_measured"
    assert builds["export"]["unavailable_reason"] == "package_unverifiable"
    assert builds["stages"][0]["record"]["state"] == "not_measured"


@pytest.mark.parametrize(
    "mutation",
    [
        "evaluated_run_id",
        "closed_run_id",
        "not_run_run_id",
        "not_run_build",
        "build_missing",
        "build_duplicate",
        "build_mismatch",
    ],
)
def test_v4_schema_binds_stage_ids_and_builds_to_execution_status(mutation: str) -> None:
    execution = json.loads((FIXTURE_V2 / "paired/execution.json").read_bytes())
    rows = tuple(
        json.loads(line) for line in (FIXTURE_V2 / "paired/rows.jsonl").read_text().splitlines()
    )
    trajectory = json.loads((FIXTURE_V2 / "trajectory/trajectory.json").read_bytes())
    identities = {
        path: (
            "sha256:" + hashlib.sha256((FIXTURE_V2 / path).read_bytes()).hexdigest(),
            (FIXTURE_V2 / path).stat().st_size,
        )
        for path in MEMBERS
    }
    report = cast(
        dict[str, Any], analyze_goppi_trajectory(execution, rows, trajectory, identities)
    )
    validator = cast(Validator, packaged_validator(RESULT_SCHEMA_V4))
    if mutation == "evaluated_run_id":
        next(stage for stage in report["stages"] if stage["execution_status"] == "evaluated")[
            "stage_run_id"
        ] = None
    elif mutation == "closed_run_id":
        next(stage for stage in report["stages"] if stage["execution_status"] == "closed")[
            "stage_run_id"
        ] = None
    elif mutation == "not_run_run_id":
        next(stage for stage in report["stages"] if stage["execution_status"] == "not_run")[
            "stage_run_id"
        ] = "sha256:" + "a" * 64
    elif mutation == "not_run_build":
        next(
            item
            for item in report["producer_declarations"]["builds"]["stages"]
            if item["execution_status"] == "not_run"
        )["record"] = {
            "state": "not_measured",
            "record_state": "not_recorded",
            "package_name": None,
            "package_version": None,
            "identity_assurance": None,
            "dir_hash": None,
            "unavailable_reason": None,
        }
    elif mutation == "build_missing":
        report["producer_declarations"]["builds"]["stages"].pop()
    elif mutation == "build_duplicate":
        builds = report["producer_declarations"]["builds"]["stages"]
        builds[-1] = copy.deepcopy(builds[0])
    else:
        next(
            item
            for item in report["producer_declarations"]["builds"]["stages"]
            if item["stage"] == "held_in"
        )["execution_status"] = "evaluated"
    with pytest.raises(ValidationError):
        validator.validate(report)


@pytest.mark.parametrize(
    "mutation", ["runner_profile", "runner_default_level", "synthetic_split_limit"]
)
def test_v4_schema_binds_runner_and_split_declaration_conditions(mutation: str) -> None:
    execution = json.loads((FIXTURE_V2 / "paired/execution.json").read_bytes())
    rows = tuple(
        json.loads(line) for line in (FIXTURE_V2 / "paired/rows.jsonl").read_text().splitlines()
    )
    trajectory = json.loads((FIXTURE_V2 / "trajectory/trajectory.json").read_bytes())
    identities = {
        path: (
            "sha256:" + hashlib.sha256((FIXTURE_V2 / path).read_bytes()).hexdigest(),
            (FIXTURE_V2 / path).stat().st_size,
        )
        for path in MEMBERS
    }
    report = cast(
        dict[str, Any], analyze_goppi_trajectory(execution, rows, trajectory, identities)
    )
    validator = cast(Validator, packaged_validator(RESULT_SCHEMA_V4))
    if mutation == "runner_profile":
        report["producer_declarations"]["runner"]["profile"] = "pi_1_3_0"
    elif mutation == "runner_default_level":
        report["producer_declarations"]["runner"]["thinking_level_source"] = "runner_default"
        report["producer_declarations"]["runner"]["thinking_level"] = "low"
    else:
        report["producer_declarations"]["split"]["limitations"] = []
    with pytest.raises(ValidationError):
        validator.validate(report)


def _v2_report(
    report: dict[str, Any],
    *,
    accepted_source: str | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(report)
    result["schema"] = RESULT_SCHEMA_V2
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


STOPPED_LIMIT = "stopped_cycle_absent_stages_may_be_unrecorded"
STOP_REASON_LIMIT = "stopped_cycle_stop_reason_not_exported"
DIGEST_ONLY_LIMIT = "evaluated_stage_verdicts_are_digest_only"
STOPPED_TASK = "obtain_stopped_cycle_stage_record"
VERDICT_TASK = "obtain_evaluated_stage_verdict"
ACCEPTED_TRUST = {
    "verifier_report_state": "report_accepted",
    "verifier_source": "operator_configured_executable",
    "verifier_identity_verified": False,
    "response_snapshot_binding": "selected_fields_matched",
    "snapshot_postcheck": "unchanged",
    "content_closure": "verifier_reported_valid",
    "authenticity": "verifier_reported_content_digest_only",
}


def _current(shape: str = "recorded", *, outcome_actions: bool = False) -> dict[str, Any]:
    """Run the pure owner on consistent fixture variants; never hand-assemble a report."""
    execution = json.loads((FIXTURE / "paired/execution.json").read_bytes())
    rows = tuple(
        json.loads(line) for line in (FIXTURE / "paired/rows.jsonl").read_text().splitlines()
    )
    trajectory = json.loads((FIXTURE / "trajectory/trajectory.json").read_bytes())
    if shape in {"stopped_before_held_out", "completed_without_held_out"}:
        execution["stages"] = [s for s in execution["stages"] if s["stage"] != "held_out"]
        rows = tuple(row for row in rows if row["stage"] != "held_out")
        trajectory["units"] = [u for u in trajectory["units"] if u["unit"]["stage"] != "held_out"]
        trajectory["availability_counts"].update(retained=len(rows), total=len(rows))
        if shape == "completed_without_held_out":
            execution["source"]["cycle_status"] = "completed"
    elif shape == "closed_same_batch":
        execution["stages"] = [execution["stages"][0]]
        execution["stages"][0].update(
            execution_status="closed",
            exported_row_count=0,
            reason_code="budget_stop",
            classification="indeterminate",
        )
        execution["claims"]["outcome_harness_binding"] = "not_applicable_no_evaluated_stages"
        rows = ()
        trajectory.update(
            units=[],
            closed_stages=["same_batch"],
            availability_counts=dict.fromkeys((*AVAILABILITIES, "total"), 0),
        )
    return cast(
        dict[str, Any],
        review_goppi_trajectory(
            execution,
            rows,
            trajectory,
            {
                p: (
                    "sha256:" + hashlib.sha256((FIXTURE / p).read_bytes()).hexdigest(),
                    (FIXTURE / p).stat().st_size,
                )
                for p in MEMBERS
            },
            outcome_actions=outcome_actions,
        ),
    )


def _v3_validator(outcome_actions: bool = False) -> Validator:
    return cast(
        Validator,
        packaged_validator(OUTCOME_ACTIONS_SCHEMA_V3 if outcome_actions else RESULT_SCHEMA_V3),
    )


SHAPES = ("recorded", "stopped_before_held_out", "completed_without_held_out", "closed_same_batch")


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("outcome_actions", [False, True])
@pytest.mark.parametrize("accepted", [False, True])
def test_v3_contracts_accept_pure_and_report_accepted_results(
    shape: str, outcome_actions: bool, accepted: bool
) -> None:
    name = OUTCOME_ACTIONS_SCHEMA_V3 if outcome_actions else RESULT_SCHEMA_V3
    authored = (ROOT / "contracts" / f"{name}.schema.json").read_bytes()
    assert authored == (ROOT / "src/underwrite/_contracts" / f"{name}.schema.json").read_bytes()
    report = _current(shape, outcome_actions=outcome_actions)
    if accepted:
        report["trust_scope"].update(ACCEPTED_TRUST)
    _v3_validator(outcome_actions).validate(report)


def _cycle_task(report: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(item for item in report["next_verification_tasks"] if item["kind"] == kind)


def _drop_task(report: dict[str, Any], kind: str) -> None:
    report["next_verification_tasks"] = [
        item for item in report["next_verification_tasks"] if item["kind"] != kind
    ]


def _hide_absent_stage(report: dict[str, Any]) -> None:
    report["cycle"]["stages_absent_from_export"] = []
    report["interpretation_limits"].remove(STOPPED_LIMIT)
    _drop_task(report, STOPPED_TASK)


def _complete_with_stop_claims(report: dict[str, Any]) -> None:
    report["cycle"]["status"] = "completed"
    report["interpretation_limits"].remove(STOPPED_LIMIT)
    report["interpretation_limits"].remove(STOP_REASON_LIMIT)


def _stop_task_names(report: dict[str, Any], stage: str) -> None:
    scope = _cycle_task(report, STOPPED_TASK)["scope"]
    scope.append({**scope[0], "stage": stage})


def _close_first_stage(report: dict[str, Any]) -> None:
    report["stages"][0].update(
        execution_status="closed", reason_code="budget_stop", classification="indeterminate"
    )


Mutation = Callable[[dict[str, Any]], object]
# shape -> mutation pairs that the contract must refuse.
V3_REFUSALS: dict[str, tuple[str, Mutation]] = {
    "drop_digest_only_limit": (
        "recorded",
        lambda r: r["interpretation_limits"].remove(DIGEST_ONLY_LIMIT),
    ),
    "drop_stop_reason_limit": (
        "recorded",
        lambda r: r["interpretation_limits"].remove(STOP_REASON_LIMIT),
    ),
    "stop_reason_limit_with_closed_stage": (
        "closed_same_batch",
        lambda r: r["interpretation_limits"].append(STOP_REASON_LIMIT),
    ),
    "absent_limit_without_absent_stage": (
        "recorded",
        lambda r: r["interpretation_limits"].insert(-2, STOPPED_LIMIT),
    ),
    "absent_stage_without_absent_limit": (
        "stopped_before_held_out",
        lambda r: r["interpretation_limits"].remove(STOPPED_LIMIT),
    ),
    "absent_stage_without_stop_task": (
        "stopped_before_held_out",
        lambda r: _drop_task(r, STOPPED_TASK),
    ),
    "stop_claims_for_completed_cycle": ("stopped_before_held_out", _complete_with_stop_claims),
    "hidden_absent_stage": ("stopped_before_held_out", _hide_absent_stage),
    "recorded_stage_listed_absent": (
        "completed_without_held_out",
        lambda r: r["cycle"].update(stages_absent_from_export=["held_in", "held_out"]),
    ),
    "stop_task_names_recorded_stage": (
        "stopped_before_held_out",
        lambda r: _stop_task_names(r, "same_batch"),
    ),
    "stop_task_without_cycle_run_id": (
        "stopped_before_held_out",
        lambda r: _cycle_task(r, STOPPED_TASK)["scope"][0].pop("cycle_run_id"),
    ),
    "verdict_task_omits_evaluated_stage": (
        "recorded",
        lambda r: _cycle_task(r, VERDICT_TASK)["scope"].pop(),
    ),
    "verdict_task_names_absent_stage": (
        "stopped_before_held_out",
        lambda r: _cycle_task(r, VERDICT_TASK)["scope"].append(
            {"stage": "held_out", "decision_digest": "sha256:" + "a" * 64}
        ),
    ),
    "reordered_limits": ("recorded", lambda r: r["interpretation_limits"].reverse()),
    "unknown_cycle_status": ("recorded", lambda r: r["cycle"].update(status="paused")),
    "evaluated_without_decision": ("recorded", lambda r: r["stages"][0].pop("decision_digest")),
    "evaluated_reported_verdict": (
        "recorded",
        lambda r: r["stages"][0].update(verdict_state="producer_reported"),
    ),
    "closed_digest_only_verdict": ("recorded", _close_first_stage),
    "verdict_task_without_digest": (
        "recorded",
        lambda r: _cycle_task(r, VERDICT_TASK)["scope"][0].pop("decision_digest"),
    ),
    "verdict_task_owner": (
        "recorded",
        lambda r: _cycle_task(r, VERDICT_TASK).update(owner="calling_agent"),
    ),
    "runner_extra": ("recorded", lambda r: r["runner"]["models"][0].update(verified=True)),
    "runner_negative": ("recorded", lambda r: r["runner"].update(model_unrecorded_rows=-1)),
    "duplicate_absent_stage": (
        "stopped_before_held_out",
        lambda r: r["cycle"].update(stages_absent_from_export=["held_out", "held_out"]),
    ),
    "legacy_trust": (
        "recorded",
        lambda r: r["trust_scope"].update(native_verification="not_performed"),
    ),
}


@pytest.mark.parametrize("mutation", sorted(V3_REFUSALS))
def test_v3_contract_refuses_unbound_cycle_and_verdict_claims(mutation: str) -> None:
    shape, change = V3_REFUSALS[mutation]
    report = _current(shape)
    _v3_validator().validate(report)
    change(report)
    with pytest.raises(ValidationError):
        _v3_validator().validate(report)


def test_retained_v2_review_contract_bytes_remain_pinned() -> None:
    """Stored layout-v2 records keep their v2 analyses; the contract cannot drift."""
    raw = (ROOT / "contracts" / f"{RESULT_SCHEMA_V2}.schema.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "0108a28992026f4a426cd4e75a6f63ec8dec9d837c832e53fdb932916fa590f0"
    )
