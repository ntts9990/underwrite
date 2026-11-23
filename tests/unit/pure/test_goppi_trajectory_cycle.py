"""Current review surfaces producer cycle, stage-verdict and runner claims on synthetic copies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes

from underwrite.instrument.evidence.goppi_trajectory import (
    AVAILABILITIES,
    MEMBERS,
    OUTCOME_ACTIONS_POLICY_V2,
    OUTCOME_ACTIONS_SCHEMA_V3,
    RESULT_SCHEMA_V3,
    REVIEW_POLICY_V2,
    VERIFIER_REPORT_NOT_PERFORMED,
    TrajectoryError,
    analyze_goppi_trajectory,
    analyze_goppi_trajectory_actions,
    review_goppi_trajectory,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/conformance/goppi-trajectory/exchange"
Documents = tuple[
    dict[str, Any], tuple[dict[str, Any], ...], dict[str, Any], dict[str, tuple[str, int]]
]
STOPPED_LIMIT = "stopped_cycle_absent_stages_may_be_unrecorded"
STOP_REASON_LIMIT = "stopped_cycle_stop_reason_not_exported"
DIGEST_ONLY_LIMIT = "evaluated_stage_verdicts_are_digest_only"
CLAIM_LIMIT = "cycle_status_stage_verdicts_and_runner_are_producer_claims"
STOPPED_TASK = "obtain_stopped_cycle_stage_record"
VERDICT_TASK = "obtain_evaluated_stage_verdict"
OUTCOME_TASK = "inspect_outcome_change_without_tool_status_change"


@pytest.fixture
def documents() -> Documents:
    identities = {
        path: (
            "sha256:" + hashlib.sha256((FIXTURE / path).read_bytes()).hexdigest(),
            (FIXTURE / path).stat().st_size,
        )
        for path in MEMBERS
    }
    return (
        json.loads((FIXTURE / "paired/execution.json").read_bytes()),
        tuple(
            json.loads(line) for line in (FIXTURE / "paired/rows.jsonl").read_text().splitlines()
        ),
        json.loads((FIXTURE / "trajectory/trajectory.json").read_bytes()),
        identities,
    )


def review(documents: Documents, *, outcome_actions: bool = False) -> dict[str, Any]:
    return cast(
        dict[str, Any], review_goppi_trajectory(*documents, outcome_actions=outcome_actions)
    )


def drop_stage(documents: Documents, name: str) -> Documents:
    """Remove one recorded stage consistently, as an export stopped before it would read."""
    execution, rows, trajectory, identities = documents
    execution["stages"] = [stage for stage in execution["stages"] if stage["stage"] != name]
    kept = tuple(row for row in rows if row["stage"] != name)
    trajectory["units"] = [unit for unit in trajectory["units"] if unit["unit"]["stage"] != name]
    retained = sum(
        len(unit["cases"])
        for unit in trajectory["units"]
        if unit["source_availability"] == "retained"
    )
    trajectory["availability_counts"].update(retained=retained, total=len(kept))
    return execution, kept, trajectory, identities


def kinds(report: dict[str, Any]) -> list[str]:
    return [task["kind"] for task in report["next_verification_tasks"]]


def task(report: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(item for item in report["next_verification_tasks"] if item["kind"] == kind)


def test_current_review_displays_cycle_runner_and_digest_only_verdicts(
    documents: Documents,
) -> None:
    execution, rows, trajectory, _ = documents
    report = review(documents)
    assert report["schema"] == RESULT_SCHEMA_V3 == "goppi_trajectory_review.v3"
    assert report["policy"] == REVIEW_POLICY_V2
    assert report["cycle"] == {
        "status": execution["source"]["cycle_status"],
        "stages_absent_from_export": [],
    }
    assert report["cycle"]["status"] == "stopped"
    model = trajectory["units"][0]["context"]["runner_model"]
    assert report["runner"] == {
        "profile": execution["source"]["runner"]["profile"],
        "models": [{"runner_model": model, "paired_rows": len(rows)}],
        "model_unrecorded_rows": 0,
    }
    decisions = {stage["stage"]: stage["decision_digest"] for stage in execution["stages"]}
    for stage in report["stages"]:
        assert stage["execution_status"] == "evaluated"
        assert stage["verdict_state"] == "digest_only"
        assert stage["decision_digest"] == decisions[stage["stage"]]
        assert "classification" not in stage
    assert task(report, VERDICT_TASK)["scope"] == [
        {"stage": name, "decision_digest": decisions[name]}
        for name in ("same_batch", "held_in", "held_out")
    ]
    assert task(report, VERDICT_TASK)["owner"] == "goppi"
    # Every supported stage is recorded, so a stop cannot hide an unrecorded later stage,
    # but with no closed stage the export still records no reason for the stop.
    assert STOPPED_TASK not in kinds(report)
    assert STOPPED_LIMIT not in report["interpretation_limits"]
    assert report["interpretation_limits"][-3:] == [
        CLAIM_LIMIT,
        STOP_REASON_LIMIT,
        DIGEST_ONLY_LIMIT,
    ]
    assert all(item["execution_authorized"] is False for item in report["next_verification_tasks"])
    assert kinds(report) == sorted(kinds(report))
    assert report["trust_scope"] == {
        **VERIFIER_REPORT_NOT_PERFORMED,
        "source_authentication_independently_verified": False,
        "producer_projection_independently_verified": False,
        "outcome_harness_binding": execution["claims"]["outcome_harness_binding"],
    }


def test_current_review_keeps_every_v1_interpretation_unchanged(documents: Documents) -> None:
    before = analyze_goppi_trajectory(*documents)
    report = review(documents)
    for field in (
        "state",
        "input_members",
        "source_context",
        "populations",
        "capture_counts",
        "comparisons",
        "authority",
    ):
        assert report[field] == before[field]
    previous = cast(list[dict[str, Any]], before["stages"])
    assert [
        {k: v for k, v in stage.items() if k not in {"verdict_state", "decision_digest"}}
        for stage in report["stages"]
    ] == previous
    comparison_tasks = [
        item for item in report["next_verification_tasks"] if item["kind"] != VERDICT_TASK
    ]
    assert comparison_tasks == before["next_verification_tasks"]
    previous_limits = cast(list[str], before["interpretation_limits"])
    assert report["interpretation_limits"][: len(previous_limits)] == previous_limits
    # The pure v1 calculation stays byte-stable for its existing callers.
    assert hashlib.sha256(canonical_bytes(before)).hexdigest() == (
        "303c37e3d86e439af3344dc99d9035ad37f75acfd1d26e732875dafc38438980"
    )
    assert hashlib.sha256(
        canonical_bytes(analyze_goppi_trajectory_actions(*documents))
    ).hexdigest() == "36d22a2d902ad2ca2c7a85b6b49704c8fe86827b3100e93f41671d3b988f4878"


def test_stopped_cycle_with_absent_stage_requests_its_record(documents: Documents) -> None:
    report = review(drop_stage(documents, "held_out"))
    assert report["cycle"] == {"status": "stopped", "stages_absent_from_export": ["held_out"]}
    assert [stage["stage"] for stage in report["stages"]] == ["same_batch", "held_in"]
    stopped = task(report, STOPPED_TASK)
    # The cycle run ID scopes the request, so equal absences in other cycles stay distinct.
    cycle_run_id = report["source_context"]["cycle_run_id"]
    assert stopped["scope"] == [{"cycle_run_id": cycle_run_id, "stage": "held_out"}]
    assert stopped["owner"] == "goppi" and stopped["execution_authorized"] is False
    assert report["interpretation_limits"][-4:] == [
        CLAIM_LIMIT,
        STOPPED_LIMIT,
        STOP_REASON_LIMIT,
        DIGEST_ONLY_LIMIT,
    ]
    assert [item["stage"] for item in task(report, VERDICT_TASK)["scope"]] == [
        "same_batch",
        "held_in",
    ]


def test_completed_cycle_reports_absence_without_a_stop_claim(documents: Documents) -> None:
    documents[0]["source"]["cycle_status"] = "completed"
    report = review(drop_stage(documents, "held_out"))
    assert report["cycle"] == {"status": "completed", "stages_absent_from_export": ["held_out"]}
    assert STOPPED_TASK not in kinds(report)
    assert STOPPED_LIMIT not in report["interpretation_limits"]
    assert STOP_REASON_LIMIT not in report["interpretation_limits"]


def test_cycle_tasks_do_not_depend_on_comparisons(documents: Documents) -> None:
    execution, _, trajectory, identities = documents
    execution["stages"] = [execution["stages"][0]]
    execution["stages"][0].update(
        execution_status="closed",
        exported_row_count=0,
        reason_code="budget_stop",
        classification="indeterminate",
    )
    trajectory.update(
        units=[],
        closed_stages=["same_batch"],
        availability_counts=dict.fromkeys((*AVAILABILITIES, "total"), 0),
    )
    execution["claims"]["outcome_harness_binding"] = "not_applicable_no_evaluated_stages"
    report = review((execution, (), trajectory, identities))
    assert report["comparisons"] == []
    assert report["stages"][0]["verdict_state"] == "producer_reported"
    assert report["stages"][0]["classification"] == "indeterminate"
    assert report["stages"][0]["reason_code"] == "budget_stop"
    assert "decision_digest" not in report["stages"][0]
    assert report["cycle"] == {
        "status": "stopped",
        "stages_absent_from_export": ["held_in", "held_out"],
    }
    assert kinds(report) == [STOPPED_TASK]
    assert [item["stage"] for item in task(report, STOPPED_TASK)["scope"]] == [
        "held_in",
        "held_out",
    ]
    assert report["runner"]["models"] == [] and report["runner"]["model_unrecorded_rows"] == 0
    assert DIGEST_ONLY_LIMIT not in report["interpretation_limits"]
    # The closed stage exports its own reason, so the stop is not unexplained.
    assert STOP_REASON_LIMIT not in report["interpretation_limits"]
    assert report["interpretation_limits"][-2:] == [CLAIM_LIMIT, STOPPED_LIMIT]


def test_runner_models_partition_rows_and_keep_unrecorded_models(documents: Documents) -> None:
    _, rows, trajectory, _ = documents
    first, second = trajectory["units"][0], trajectory["units"][1]
    first["context"]["runner_model"] = "synthetic-model-b"
    second["context"].pop("runner_model")
    report = review(documents)
    counts = {
        item["runner_model"]: item["paired_rows"] for item in report["runner"]["models"]
    }
    assert counts["synthetic-model-b"] == len(first["cases"])
    assert report["runner"]["model_unrecorded_rows"] == len(second["cases"])
    assert sum(counts.values()) + report["runner"]["model_unrecorded_rows"] == len(rows)
    assert [item["runner_model"] for item in report["runner"]["models"]] == sorted(counts)


def test_unavailable_capture_without_context_has_no_runner_claim(documents: Documents) -> None:
    trajectory = documents[2]
    unit = trajectory["units"][0]
    unit.update(
        source_availability="not_retained",
        unavailable_reason="capture_policy_unrecorded",
        capture_completeness="incomplete",
        capture_profile=None,
        context=None,
    )
    for case in unit["cases"]:
        case.update(spans=[], verifier_observation=None)
    trajectory["availability_counts"]["retained"] -= len(unit["cases"])
    trajectory["availability_counts"]["not_retained"] += len(unit["cases"])
    report = review(documents)
    assert report["runner"]["model_unrecorded_rows"] == len(unit["cases"])


def test_outcome_actions_view_adds_the_same_cycle_disclosure(documents: Documents) -> None:
    ordinary = review(documents)
    actions = review(documents, outcome_actions=True)
    assert actions["schema"] == OUTCOME_ACTIONS_SCHEMA_V3 == "goppi_trajectory_outcome_actions.v3"
    assert actions["policy"] == OUTCOME_ACTIONS_POLICY_V2
    assert OUTCOME_TASK in kinds(actions) and VERDICT_TASK in kinds(actions)
    assert kinds(actions) == sorted(kinds(actions))
    for field in ("cycle", "runner", "stages", "interpretation_limits", "trust_scope"):
        assert actions[field] == ordinary[field]
    shared = [item for item in actions["next_verification_tasks"] if item["kind"] != OUTCOME_TASK]
    assert shared == ordinary["next_verification_tasks"]
    assert task(actions, OUTCOME_TASK) == next(
        item
        for item in cast(
            list[dict[str, Any]],
            analyze_goppi_trajectory_actions(*documents)["next_verification_tasks"],
        )
        if item["kind"] == OUTCOME_TASK
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("unknown_status", "TRAJECTORY_UNSUPPORTED_VALUE"),
        ("missing_status", "TRAJECTORY_VALUE_INVALID"),
        ("missing_decision", "TRAJECTORY_VALUE_INVALID"),
        ("malformed_decision", "TRAJECTORY_VALUE_INVALID"),
    ],
)
def test_current_review_refuses_unreadable_cycle_and_verdict_claims(
    documents: Documents, mutation: str, reason: str
) -> None:
    execution = documents[0]
    if mutation == "unknown_status":
        execution["source"]["cycle_status"] = "paused"
    elif mutation == "missing_status":
        execution["source"].pop("cycle_status")
    elif mutation == "missing_decision":
        execution["stages"][1].pop("decision_digest")
    else:
        execution["stages"][1]["decision_digest"] = "sha256:short"
    with pytest.raises(TrajectoryError) as error:
        review(documents)
    assert error.value.reason == reason
    # The historical pure calculation never read these fields and keeps its admission.
    analyze_goppi_trajectory(*documents)


def test_reordering_preserves_cycle_tasks_and_runner(documents: Documents) -> None:
    dropped = drop_stage(documents, "held_out")
    before = review(dropped)
    execution, rows, trajectory, identities = dropped
    execution["stages"].reverse()
    trajectory["units"].reverse()
    after = review((execution, tuple(reversed(rows)), trajectory, identities))
    assert after["next_verification_tasks"] == before["next_verification_tasks"]
    assert after["cycle"] == before["cycle"] and after["runner"] == before["runner"]
    assert after["interpretation_limits"] == before["interpretation_limits"]
