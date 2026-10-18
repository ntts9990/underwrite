"""Consumer semantics on decoded synthetic copies; no native verification claim."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from underwrite.instrument.evidence.goppi_trajectory import (
    AVAILABILITIES,
    MAX_ROWS,
    MAX_SPANS_PER_UNIT,
    MAX_UNITS,
    MEMBERS,
    TrajectoryError,
    analyze_goppi_trajectory,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/conformance/goppi-trajectory"
Documents = tuple[
    dict[str, Any], tuple[dict[str, Any], ...], dict[str, Any], dict[str, tuple[str, int]]
]


@pytest.fixture
def documents() -> Documents:
    root = FIXTURE / "exchange"
    identities = {
        path: (
            "sha256:" + hashlib.sha256((root / path).read_bytes()).hexdigest(),
            (root / path).stat().st_size,
        )
        for path in MEMBERS
    }
    return (
        json.loads((root / "paired/execution.json").read_bytes()),
        tuple(json.loads(line) for line in (root / "paired/rows.jsonl").read_text().splitlines()),
        json.loads((root / "trajectory/trajectory.json").read_bytes()),
        identities,
    )


def analyze(documents: Documents) -> dict[str, Any]:
    return analyze_goppi_trajectory(*documents)


def first_case(documents: Documents) -> dict[str, Any]:
    return documents[2]["units"][0]["cases"][0]


def target(report: dict[str, Any], documents: Documents) -> dict[str, Any]:
    row = documents[1][0]
    return next(
        c for c in report["comparisons"] if all(c["key"][field] == row[field] for field in c["key"])
    )


def test_pinned_bytes_and_separate_populations(documents: Documents) -> None:
    pins = json.loads((FIXTURE / "pins.json").read_bytes())
    assert {
        k: {"sha256": digest, "bytes": size} for k, (digest, size) in documents[3].items()
    } == pins["members"]
    report = analyze(documents)
    rows = documents[1]
    assert report["populations"] == {
        "paired_rows": len(rows),
        "case_stage_repeat_pairs": len(rows) // 2,
        "unique_source_case_ids": len({r["case_id"] for r in rows}),
        "stage_case_slots": len({(r["stage"], r["case_id"]) for r in rows}),
        "primary_repeat_pairs": sum(r["primary_inference"] for r in rows) // 2,
    }
    assert (
        report["populations"]["unique_source_case_ids"]
        < report["populations"]["stage_case_slots"]
        < report["populations"]["case_stage_repeat_pairs"]
    )
    changed = target(report, documents)
    assert changed["baseline"]["outcome"]["verifier_value"] is False
    assert changed["candidate"]["outcome"]["verifier_value"] is True
    assert changed["comparability"] == {"state": "comparable", "reasons": []}
    assert changed["difference"]["tool_status_deltas"] == [
        {"tool_name": "read", "status": "error", "candidate_minus_baseline": -1},
        {"tool_name": "read", "status": "ok", "candidate_minus_baseline": 1},
    ]
    assert len(report["next_verification_tasks"]) < len(report["comparisons"])
    assert all(t["execution_authorized"] is False for t in report["next_verification_tasks"])
    assert report["trust_scope"]["authenticity"] == "not_checked"
    assert "causality_and_retry_effects_not_established" in report["interpretation_limits"]
    assert any(
        c["difference"]["tool_status_deltas"] == [] and not c["difference"]["outcome_changed"]
        for c in report["comparisons"]
    )


def test_reordering_preserves_aggregates_ids_tasks_and_valid_pointers(documents: Documents) -> None:
    before = analyze(documents)
    execution, rows, trajectory, identities = documents
    rows = tuple(reversed(rows))
    trajectory["units"].reverse()
    for unit in trajectory["units"]:
        unit["cases"].reverse()
        for case in unit["cases"]:
            case["spans"].reverse()
    after = analyze((execution, rows, trajectory, identities))
    assert after["next_verification_tasks"] == before["next_verification_tasks"]
    assert after["populations"] == before["populations"]
    for a, b in zip(after["comparisons"], before["comparisons"], strict=True):
        assert a["id"] == b["id"]
        assert a["difference"] == b["difference"]
        for arm in ("baseline", "candidate"):
            parts = a[arm]["case_pointer"].split("/")
            assert a[arm]["context_pointer"] == f"/units/{parts[2]}/context"
            assert "context" not in a[arm]
            case = trajectory["units"][int(parts[2])]["cases"][int(parts[4])]
            row: dict[str, Any] = rows[int(a[arm]["row_index"])]
            assert case["row_key"]["case_id"] == a["key"]["case_id"] == row["case_id"]
            assert case["row_key"]["stage"] == row["stage"] == a["key"]["stage"]


@pytest.mark.parametrize(
    "availability,reason",
    [
        ("not_retained", "capture_policy_unrecorded"),
        ("unsupported", "profile_not_supported"),
        ("interrupted", "pi_lifecycle_interrupted"),
        ("malformed", "pi_lifecycle_malformed"),
        ("storage_failure", "source_storage_write_failed"),
    ],
)
def test_unavailable_coverage_counts_cases_not_units(
    documents: Documents, availability: str, reason: str
) -> None:
    trajectory = documents[2]
    unit = trajectory["units"][0]
    unit.update(
        source_availability=availability,
        unavailable_reason=reason,
        capture_completeness="incomplete",
        capture_profile=None,
        context=None,
    )
    for case in unit["cases"]:
        case.update(spans=[], verifier_observation=None)
    count = len(unit["cases"])
    trajectory["availability_counts"]["retained"] -= count
    trajectory["availability_counts"][availability] += count
    report = analyze(documents)
    assert report["capture_counts"][availability] == count
    item = target(report, documents)
    assert item["baseline"]["tool_status_counts"] is None
    assert item["baseline"]["llm_span_count"] is None
    assert item["baseline"]["verifier_process"] == {"state": "unlinked"}
    assert item["baseline"]["unavailable_reason"] == reason
    assert item["difference"]["state"] == "unavailable"
    assert item["comparability"]["state"] == "unknown"
    assert "obtain_supported_capture" in {t["kind"] for t in report["next_verification_tasks"]}


def test_retained_zero_is_observed_zero_and_not_observed_is_not_unlinked(
    documents: Documents,
) -> None:
    case = first_case(documents)
    case["spans"] = []
    case["verifier_observation"] = {"case_id": case["row_key"]["case_id"], "status": "not_observed"}
    item = target(analyze(documents), documents)
    assert item["baseline"]["tool_status_counts"] == []
    assert item["baseline"]["llm_span_count"] == 0
    assert item["baseline"]["verifier_process"] == {"state": "not_observed"}


@pytest.mark.parametrize(
    "outcome",
    [
        {
            "status": "observed",
            "value_kind": "scalar",
            "scored_value": 0.25,
            "verifier_value": 0.25,
        },
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
    ],
)
def test_outcomes_preserve_source_meaning(documents: Documents, outcome: dict[str, Any]) -> None:
    documents[1][0]["result"] = outcome
    item = target(analyze(documents), documents)
    assert item["baseline"]["outcome"] == outcome
    assert item["comparability"]["state"] != "comparable"


@pytest.mark.parametrize(
    "field", ["runner_model", "verifier_runtime_digest", "budget_policy_digest", "declared_repeats"]
)
def test_context_differences_are_not_comparable(documents: Documents, field: str) -> None:
    context = documents[2]["units"][0]["context"]
    context[field] = (
        3
        if field == "declared_repeats"
        else "other-model"
        if field == "runner_model"
        else "sha256:" + "a" * 64
    )
    item = target(analyze(documents), documents)
    assert item["comparability"]["state"] == "not_comparable"
    assert {"field": "context." + field, "reason": "different_context"} in item["comparability"][
        "reasons"
    ]


def test_intended_harness_changes_and_weaker_source_scope(documents: Documents) -> None:
    documents[2]["units"][0]["context"]["harness_identity_digest"] = "sha256:" + "a" * 64
    documents[0]["claims"]["outcome_harness_binding"] = "v1_not_reverified"
    report = analyze(documents)
    assert target(report, documents)["comparability"]["state"] == "comparable"
    assert report["trust_scope"]["outcome_harness_binding"] == "v1_not_reverified"


def test_verifier_difference_and_missing_context_both_sides(documents: Documents) -> None:
    documents[1][0]["verifier"]["verifier_id"] = "other-verifier"
    first_case(documents)["verifier"]["verifier_id"] = "other-verifier"
    assert target(analyze(documents), documents)["comparability"]["state"] == "not_comparable"
    for unit in documents[2]["units"]:
        unit["context"].pop("runner_model")
    item = target(analyze(documents), documents)
    assert {"field": "context.runner_model", "reason": "missing_context"} in item["comparability"][
        "reasons"
    ]


def test_closed_only_preserves_stage_coverage(documents: Documents) -> None:
    execution, _, trajectory, identities = documents
    for stage in execution["stages"]:
        stage.update(
            execution_status="closed",
            exported_row_count=0,
            reason_code="budget_stop",
            classification="indeterminate",
        )
    trajectory.update(
        units=[],
        closed_stages=[s["stage"] for s in execution["stages"]],
        availability_counts=dict.fromkeys((*AVAILABILITIES, "total"), 0),
    )
    execution["claims"]["outcome_harness_binding"] = "not_applicable_no_evaluated_stages"
    report = analyze((execution, (), trajectory, identities))
    assert not report["comparisons"]
    assert len(report["stages"]) == len(execution["stages"])
    assert all(s["planned_case_count"] > 0 and s["paired_rows"] == 0 for s in report["stages"])
    assert not report["next_verification_tasks"]


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("duplicate_case", "TRAJECTORY_DUPLICATE_IDENTITY"),
        ("missing_case", "TRAJECTORY_COVERAGE_MISMATCH"),
        ("wrong_stage", "TRAJECTORY_COVERAGE_MISMATCH"),
        ("wrong_trace", "TRAJECTORY_BINDING_MISMATCH"),
        ("wrong_verifier", "TRAJECTORY_BINDING_MISMATCH"),
        ("wrong_process", "TRAJECTORY_BINDING_MISMATCH"),
        ("duplicate_span", "TRAJECTORY_DUPLICATE_IDENTITY"),
        ("boolean_repeat", "TRAJECTORY_VALUE_INVALID"),
        ("invalid_binary", "TRAJECTORY_VALUE_INVALID"),
        ("wrong_coverage", "TRAJECTORY_COVERAGE_MISMATCH"),
    ],
)
def test_finite_selected_field_refusals(documents: Documents, mutation: str, reason: str) -> None:
    case = first_case(documents)
    unit = documents[2]["units"][0]
    if mutation == "duplicate_case":
        unit["cases"].append(copy.deepcopy(case))
    elif mutation == "missing_case":
        unit["cases"].pop()
    elif mutation == "wrong_stage":
        case["row_key"]["stage"] = "held_in"
    elif mutation == "wrong_trace":
        case["trace_digest"] = "sha256:" + "a" * 64
    elif mutation == "wrong_verifier":
        case["verifier"]["verifier_id"] = "other-verifier"
    elif mutation == "wrong_process":
        case["verifier_observation"]["case_id"] = "other-case"
    elif mutation == "duplicate_span":
        case["spans"].append(copy.deepcopy(case["spans"][0]))
    elif mutation == "boolean_repeat":
        case["row_key"]["repeat_index"] = True
    elif mutation == "invalid_binary":
        documents[1][0]["result"]["scored_value"] = 0
    else:
        documents[2]["availability_counts"]["retained"] = True
    with pytest.raises(TrajectoryError) as error:
        analyze(documents)
    assert error.value.reason == reason or (
        mutation == "wrong_coverage" and error.value.reason == "TRAJECTORY_VALUE_INVALID"
    )
    assert error.value.code == "INVALID_INPUT"


def test_unit_span_limit_is_cumulative(documents: Documents) -> None:
    unit = documents[2]["units"][0]
    unit["cases"][0]["spans"] = [
        {"kind": "llm", "span_id": f"new-{i}"} for i in range(MAX_SPANS_PER_UNIT)
    ]
    with pytest.raises(TrajectoryError, match="TRAJECTORY_LIMIT_EXCEEDED") as error:
        analyze(documents)
    assert error.value.code == "INPUT_LIMIT_EXCEEDED"


def test_rows_and_units_limits(documents: Documents) -> None:
    with pytest.raises(TrajectoryError, match="TRAJECTORY_LIMIT_EXCEEDED") as error:
        analyze_goppi_trajectory(
            documents[0], (documents[1][0],) * (MAX_ROWS + 1), documents[2], documents[3]
        )
    assert error.value.code == "INPUT_LIMIT_EXCEEDED"
    documents[2]["units"] = [documents[2]["units"][0]] * (MAX_UNITS + 1)
    with pytest.raises(TrajectoryError, match="TRAJECTORY_LIMIT_EXCEEDED") as error:
        analyze(documents)
    assert error.value.code == "INPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize("field", ["schema", "profile", "call_relationships"])
def test_unsupported_protocol_is_not_malformed_input(documents: Documents, field: str) -> None:
    documents[2][field] = "unsupported"
    with pytest.raises(TrajectoryError, match="TRAJECTORY_UNSUPPORTED_VALUE") as error:
        analyze(documents)
    assert error.value.code == "UNSUPPORTED_FORMAT"


def test_wrong_row_container_is_not_a_resource_limit(documents: Documents) -> None:
    malformed: Any = list(documents[1])
    with pytest.raises(TrajectoryError, match="TRAJECTORY_SHAPE_INVALID") as error:
        analyze_goppi_trajectory(documents[0], malformed, documents[2], documents[3])
    assert error.value.code == "INVALID_INPUT"


def test_raw_payloads_are_not_projected(documents: Documents) -> None:
    first_case(documents)["raw_prompt"] = "PRIVATE_CANARY"
    first_case(documents)["spans"][0]["raw_response"] = "PRIVATE_CANARY"
    assert "PRIVATE_CANARY" not in json.dumps(analyze(documents))


def test_nonfinite_scalar_is_refused(documents: Documents) -> None:
    documents[1][0]["result"].update(value_kind="scalar", verifier_value=float("nan"))
    with pytest.raises(TrajectoryError, match="TRAJECTORY_VALUE_INVALID"):
        analyze(documents)


def test_pure_analysis_never_claims_native_execution(documents: Documents) -> None:
    trust = analyze(documents)["trust_scope"]
    assert trust["native_verification"] == "not_performed"
    assert trust["integrity_scope"] == "not_checked"
    assert trust["authenticity"] == "not_checked"


def test_missing_entire_arm_is_not_silently_dropped(documents: Documents) -> None:
    execution, rows, trajectory, identities = documents
    removed = trajectory["units"].pop(0)
    keys = {tuple(sorted(case["row_key"].items())) for case in removed["cases"]}
    kept = tuple(
        row
        for row in rows
        if tuple(sorted((field, row[field]) for field in removed["cases"][0]["row_key"]))
        not in keys
    )
    trajectory["availability_counts"]["retained"] = len(kept)
    trajectory["availability_counts"]["total"] = len(kept)
    with pytest.raises(TrajectoryError, match="TRAJECTORY_COVERAGE_MISMATCH"):
        analyze((execution, kept, trajectory, identities))


@pytest.mark.parametrize("value", [float("inf"), 10**400])
def test_unrepresentable_scalar_is_finite_refusal(documents: Documents, value: float | int) -> None:
    documents[1][0]["result"].update(value_kind="scalar", verifier_value=value)
    with pytest.raises(TrajectoryError, match="TRAJECTORY_VALUE_INVALID"):
        analyze(documents)


def test_non_nfc_identity_is_finite_refusal(
    documents: Documents,
) -> None:
    execution, rows, trajectory, identities = documents
    original = rows[0]["case_id"]
    for row in rows:
        if row["case_id"] == original:
            row["case_id"] = "e\u0301"
    for unit in trajectory["units"]:
        for case in unit["cases"]:
            if case["row_key"]["case_id"] == original:
                case["row_key"]["case_id"] = "e\u0301"
                case["verifier_observation"]["case_id"] = "e\u0301"
    with pytest.raises(TrajectoryError, match="TRAJECTORY_VALUE_INVALID"):
        analyze((execution, rows, trajectory, identities))
