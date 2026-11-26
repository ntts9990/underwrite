"""Consumer semantics on decoded synthetic copies; no native verification claim."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes

from underwrite.instrument.evidence import goppi_trajectory as goppi_trajectory_module
from underwrite.instrument.evidence.goppi_identity import length_prefixed_digest
from underwrite.instrument.evidence.goppi_trajectory import (
    AVAILABILITIES,
    MAX_ROWS,
    MAX_SPANS_PER_UNIT,
    MAX_UNITS,
    MEMBERS,
    RESULT_SCHEMA_V4,
    V2_COMPARABILITY_INVARIANT_FIELDS,
    TrajectoryError,
    analyze_goppi_trajectory,
    analyze_goppi_trajectory_v2,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/conformance/goppi-trajectory"
V2_FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/conformance/goppi-execution-v2"
Documents = tuple[
    dict[str, Any], tuple[dict[str, Any], ...], dict[str, Any], dict[str, tuple[str, int]]
]
EXPECTED_OUTCOME_ACTION_SCOPES = 64
V2_PAIRED_ROWS = 48
V2_REFUSAL_FIXTURES = 13
V2_SINGLE_CASE_COUNT = 1
V2_TWO_REPEAT_COUNT = 2
V2_SINGLE_CASE_TWO_REPEAT_ROWS = 2 * V2_SINGLE_CASE_COUNT * V2_TWO_REPEAT_COUNT
V2Comparability = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
V2StageParser = Callable[[dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]]
V2RepeatRunId = Callable[[str, str, str, int], str]
v2_comparability_impl = cast(
    V2Comparability, goppi_trajectory_module.__dict__["_comparability_v2"]
)
v2_stage_parser_impl = cast(V2StageParser, goppi_trajectory_module.__dict__["_stages_v2"])
v2_repeat_run_id_impl = cast(
    V2RepeatRunId, goppi_trajectory_module.__dict__["_v2_repeat_run_id"]
)


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


@pytest.fixture
def v2_documents() -> Documents:
    root = V2_FIXTURE / "paired-exchange-v2"
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


def pi15_documents(documents: Documents) -> Documents:
    execution, _, trajectory, _ = documents
    execution["source"]["runner"]["profile"] = "pi_1_5_0"
    trajectory["profile"] = "native_pi_1_5_lifecycle.v1"
    for unit in trajectory["units"]:
        if unit["source_availability"] == "retained":
            unit["capture_profile"] = trajectory["profile"]
            unit["context"]["runner_profile"] = "pi_1_5_0"
    return documents


def v2_arm_context(documents: Documents, arm: str) -> dict[str, Any]:
    row = documents[1][0]
    return next(
        unit["context"]
        for unit in documents[2]["units"]
        if unit["unit"]["arm"] == arm
        and unit["unit"]["stage"] == row["stage"]
        and unit["unit"]["repeat_index"] == row["repeat_index"]
    )


def v2_comparison_side() -> dict[str, Any]:
    return {
        "context": {
            field.removeprefix("context."): "shared"
            for field in V2_COMPARABILITY_INVARIANT_FIELDS
            if field.startswith("context.")
        }
        | {"harness_identity_digest": "sha256:" + "a" * 64},
        "row_context": {
            field.removeprefix("row."): "shared"
            for field in V2_COMPARABILITY_INVARIANT_FIELDS
            if field.startswith("row.")
        },
        "verifier": {
            field.removeprefix("verifier."): "shared"
            for field in V2_COMPARABILITY_INVARIANT_FIELDS
            if field.startswith("verifier.")
        },
        "primary_inference": True,
        "repeat_proof": "v2_attested_repeat",
        "outcome": {"value_kind": "scalar"},
    }


def _set_v2_unit_roster(unit: dict[str, Any]) -> None:
    case_ids = [case["row_key"]["case_id"] for case in unit["cases"]]
    roster_digest = length_prefixed_digest(("goppi:paired-trajectory-roster:v1", *case_ids))
    unit.update(roster_count=len(case_ids), roster_digest=roster_digest)
    context = unit["context"]
    if context is not None:
        context.update(roster_count=len(case_ids), roster_digest=roster_digest)


def v2_one_case_two_repeats(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = copy.deepcopy(documents)
    case_id = next(row["case_id"] for row in rows if row["stage"] == "same_batch")
    rows = tuple(
        row
        for row in rows
        if row["stage"] != "same_batch" or row["case_id"] == case_id
    )
    stage = next(stage for stage in execution["stages"] if stage["stage"] == "same_batch")
    stage.update(
        planned_case_count=V2_SINGLE_CASE_COUNT,
        exported_row_count=V2_SINGLE_CASE_TWO_REPEAT_ROWS,
    )
    execution["counts"].update(
        planned_stage_case_count_total=sum(
            stage["planned_case_count"] for stage in execution["stages"]
        ),
        row_count=len(rows),
    )
    for unit in trajectory["units"]:
        unit["cases"] = [case for case in unit["cases"] if case["row_key"]["case_id"] == case_id]
        _set_v2_unit_roster(unit)
    trajectory["availability_counts"] = dict.fromkeys((*AVAILABILITIES, "total"), 0)
    for unit in trajectory["units"]:
        availability = unit["source_availability"]
        case_count = len(unit["cases"])
        trajectory["availability_counts"][availability] += case_count
        trajectory["availability_counts"]["total"] += case_count
    return execution, rows, trajectory, identities


def v2_missing_case_from_repeat(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = copy.deepcopy(documents)
    missing_case_id = next(
        row["case_id"]
        for row in rows
        if row["stage"] == "same_batch" and row["repeat_index"] == 1
    )
    rows = tuple(
        row
        for row in rows
        if not (
            row["stage"] == "same_batch"
            and row["repeat_index"] == 1
            and row["case_id"] == missing_case_id
        )
    )
    stage = next(stage for stage in execution["stages"] if stage["stage"] == "same_batch")
    stage["exported_row_count"] = sum(row["stage"] == "same_batch" for row in rows)
    execution["counts"]["row_count"] = len(rows)
    for unit in trajectory["units"]:
        if unit["unit"]["repeat_index"] == 1:
            unit["cases"] = [
                case for case in unit["cases"] if case["row_key"]["case_id"] != missing_case_id
            ]
            _set_v2_unit_roster(unit)
    trajectory["availability_counts"] = dict.fromkeys((*AVAILABILITIES, "total"), 0)
    for unit in trajectory["units"]:
        availability = unit["source_availability"]
        case_count = len(unit["cases"])
        trajectory["availability_counts"][availability] += case_count
        trajectory["availability_counts"]["total"] += case_count
    return execution, rows, trajectory, identities


def v2_replace_repeat_run_id(
    documents: Documents, repeat_index: int, repeat_run_id: str
) -> Documents:
    execution, rows, trajectory, identities = copy.deepcopy(documents)
    for row in rows:
        if row["stage"] == "same_batch" and row["repeat_index"] == repeat_index:
            row["repeat_run_id"] = repeat_run_id
    for unit in trajectory["units"]:
        if unit["unit"]["stage"] == "same_batch" and unit["unit"]["repeat_index"] == repeat_index:
            unit["unit"]["repeat_run_id"] = repeat_run_id
            for case in unit["cases"]:
                case["row_key"]["repeat_run_id"] = repeat_run_id
    return execution, rows, trajectory, identities


def v2_split_repeat_index_run_ids(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = copy.deepcopy(documents)
    baseline = next(
        unit
        for unit in trajectory["units"]
        if unit["unit"]["stage"] == "same_batch"
        and unit["unit"]["arm"] == "baseline"
        and unit["unit"]["repeat_index"] == 0
    )
    case_ids = [case["row_key"]["case_id"] for case in baseline["cases"]]
    split_case_ids = set(case_ids[len(case_ids) // 2 :])
    replacement_id = "sha256:" + "f" * 64
    for row in rows:
        if (
            row["stage"] == "same_batch"
            and row["repeat_index"] == 0
            and row["case_id"] in split_case_ids
        ):
            row["repeat_run_id"] = replacement_id
    split_units: list[dict[str, Any]] = []
    for unit in trajectory["units"]:
        if unit["unit"]["stage"] != "same_batch" or unit["unit"]["repeat_index"] != 0:
            continue
        split_unit = copy.deepcopy(unit)
        split_unit["cases"] = [
            case for case in unit["cases"] if case["row_key"]["case_id"] in split_case_ids
        ]
        split_unit["unit"]["repeat_run_id"] = replacement_id
        for case in split_unit["cases"]:
            case["row_key"]["repeat_run_id"] = replacement_id
        unit["cases"] = [
            case for case in unit["cases"] if case["row_key"]["case_id"] not in split_case_ids
        ]
        _set_v2_unit_roster(unit)
        _set_v2_unit_roster(split_unit)
        split_units.append(split_unit)
    trajectory["units"].extend(split_units)
    return execution, rows, trajectory, identities


def v2_single_repeat_with_attested_proof(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = v2_one_case_two_repeats(documents)
    rows = tuple(row for row in rows if row["repeat_index"] == 0)
    stage = next(stage for stage in execution["stages"] if stage["stage"] == "same_batch")
    stage.update(authenticated_repeat_count=1, exported_row_count=2)
    execution["counts"].update(authenticated_repeat_count_total=1, row_count=len(rows))
    trajectory["units"] = [
        unit for unit in trajectory["units"] if unit["unit"]["repeat_index"] == 0
    ]
    for unit in trajectory["units"]:
        unit["context"]["declared_repeats"] = 1
    trajectory["availability_counts"] = dict.fromkeys((*AVAILABILITIES, "total"), 0)
    for unit in trajectory["units"]:
        case_count = len(unit["cases"])
        trajectory["availability_counts"][unit["source_availability"]] += case_count
        trajectory["availability_counts"]["total"] += case_count
    return execution, rows, trajectory, identities


def v2_partial_units_claiming_full_roster(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = copy.deepcopy(documents)
    unit = next(
        unit
        for unit in trajectory["units"]
        if unit["unit"]["stage"] == "same_batch"
        and unit["unit"]["arm"] == "baseline"
        and unit["unit"]["repeat_index"] == 0
    )
    duplicate = copy.deepcopy(unit)
    midpoint = len(unit["cases"]) // 2
    unit["cases"] = unit["cases"][:midpoint]
    duplicate["cases"] = duplicate["cases"][midpoint:]
    trajectory["units"].append(duplicate)
    return execution, rows, trajectory, identities


def v2_reordered_later_repeat_roster(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = copy.deepcopy(documents)
    for unit in trajectory["units"]:
        if unit["unit"]["stage"] == "same_batch" and unit["unit"]["repeat_index"] == 1:
            unit["cases"].reverse()
            _set_v2_unit_roster(unit)
    return execution, rows, trajectory, identities


def v2_stage_run_id_mismatch(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = copy.deepcopy(documents)
    forged_run_id = "sha256:" + "f" * 64
    for stage in execution["stages"]:
        if stage["stage"] == "same_batch":
            stage["stage_run_id"] = forged_run_id
    for row in rows:
        if row["stage"] == "same_batch":
            row["stage_run_id"] = forged_run_id
    for unit in trajectory["units"]:
        if unit["unit"]["stage"] == "same_batch":
            unit["unit"]["stage_run_id"] = forged_run_id
            for case in unit["cases"]:
                case["row_key"]["stage_run_id"] = forged_run_id
    return execution, rows, trajectory, identities


def v2_arbitrary_repeat_run_id(documents: Documents) -> Documents:
    return v2_replace_repeat_run_id(
        v2_one_case_two_repeats(documents), 0, "sha256:" + "f" * 64
    )


def v2_reused_repeat_run_id(documents: Documents) -> Documents:
    one_case_two_repeats = v2_one_case_two_repeats(documents)
    repeat_zero_id = next(
        row["repeat_run_id"] for row in one_case_two_repeats[1] if row["repeat_index"] == 0
    )
    return v2_replace_repeat_run_id(one_case_two_repeats, 1, repeat_zero_id)


def v2_mixed_repeat_proofs(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = v2_one_case_two_repeats(documents)
    next(
        row
        for row in rows
        if row["stage"] == "same_batch"
        and row["repeat_index"] == 0
        and row["arm"] == "baseline"
    )["repeat_proof"] = "v1_primary_only"
    return execution, rows, trajectory, identities


def v2_primary_only_proof_with_two_repeats(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = v2_one_case_two_repeats(documents)
    for row in rows:
        if row["stage"] == "same_batch":
            row["repeat_proof"] = "v1_primary_only"
    return execution, rows, trajectory, identities


def v2_declared_repeat_mismatch(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = v2_one_case_two_repeats(documents)
    trajectory["units"][0]["context"]["declared_repeats"] = 1
    return execution, rows, trajectory, identities


def v2_roster_digest_mismatch(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = copy.deepcopy(documents)
    unit = trajectory["units"][0]
    unit["roster_digest"] = "sha256:" + "f" * 64
    unit["context"]["roster_digest"] = unit["roster_digest"]
    return execution, rows, trajectory, identities


def v2_roster_context_mismatch(documents: Documents) -> Documents:
    execution, rows, trajectory, identities = copy.deepcopy(documents)
    trajectory["units"][0]["context"]["roster_digest"] = "sha256:" + "f" * 64
    return execution, rows, trajectory, identities


def first_case(documents: Documents) -> dict[str, Any]:
    return documents[2]["units"][0]["cases"][0]


def target(report: dict[str, Any], documents: Documents) -> dict[str, Any]:
    row = documents[1][0]
    return next(
        c for c in report["comparisons"] if all(c["key"][field] == row[field] for field in c["key"])
    )


def task_scopes(report: dict[str, Any], kind: str) -> dict[str, list[str]]:
    return {
        scope["comparison_id"]: scope["arms"]
        for task in report["next_verification_tasks"]
        if task["kind"] == kind
        for scope in task["scope"]
    }


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


def test_default_trajectory_report_keeps_pinned_v1_bytes(documents: Documents) -> None:
    assert hashlib.sha256(canonical_bytes(analyze(documents))).hexdigest() == (
        "303c37e3d86e439af3344dc99d9035ad37f75acfd1d26e732875dafc38438980"
    )


def test_execution_v2_keeps_build_split_and_stage_states_separate(
    v2_documents: Documents,
) -> None:
    report = analyze_goppi_trajectory(*v2_documents)
    declarations = cast(dict[str, Any], report["producer_declarations"])
    builds = cast(dict[str, Any], declarations["builds"])
    export = cast(dict[str, Any], builds["export"])
    stage_builds = cast(list[dict[str, Any]], builds["stages"])
    split = cast(dict[str, Any], declarations["split"])
    stages = cast(list[dict[str, Any]], report["stages"])
    populations = cast(dict[str, int], report["populations"])

    assert report["schema"] == RESULT_SCHEMA_V4
    assert populations["paired_rows"] == V2_PAIRED_ROWS
    assert export["state"] == "self_reported"
    assert export["dir_hash"] == "4700ec2d5679e5454bf7eaa5eb59429cc0ff452e736f4828bbc91695c64df7d4"
    assert builds["identity_assurance"] == "self_reported"
    assert builds["independently_verified"] is False
    assert builds["cross_stage_comparability"] == "not_inferred_from_digest_equality"
    assert stage_builds[-1] == {
        "stage": "held_out",
        "execution_status": "not_run",
        "record": None,
    }
    held_out = next(stage for stage in stages if stage["stage"] == "held_out")
    held_in = next(stage for stage in stages if stage["stage"] == "held_in")
    assert held_out["execution_status"] == "not_run"
    assert held_out["classification"] == "not_evaluated"
    assert held_out["not_run_reason"] == "earlier_stage_partial_stop"
    assert held_out["stage_run_id"] is None
    assert held_in["execution_status"] == "closed"
    assert held_in["classification"] == "indeterminate"
    assert split["identity_assurance"] == "self_reported"
    assert split["commitment_recomputed"] is False
    assert split["limitations"] == ["split_salt_guessable"]
    assert declarations["runner"]["identity_assurance"] == "declared"
    assert declarations["runner"]["independently_verified"] is False


EXPECTED_V2_HELD_CONSTANT = (
    "context.approved_draft_digest",
    "context.assignment_digest",
    "context.baseline_snapshot_digest",
    "context.budget_policy_digest",
    "context.candidate_snapshot_digest",
    "context.config_digest",
    "context.declared_repeats",
    "context.evalset_digest",
    "context.execution_identity_digest",
    "context.plan_digest",
    "context.primary_policy_digest",
    "context.roster_count",
    "context.roster_digest",
    "context.runner_base_identity_digest",
    "context.runner_model",
    "context.runner_profile",
    "context.split_policy_digest",
    "context.verifier_profile_identity_digest",
    "context.verifier_runtime_digest",
    "outcome.value_kind",
    "primary_inference",
    "repeat_proof",
    "row.assignment_digest",
    "row.config_digest",
    "row.evalset_digest",
    "row.split_policy_digest",
    "verifier.verifier_digest",
    "verifier.verifier_id",
)


def test_execution_v2_reports_arm_harness_as_intervention(v2_documents: Documents) -> None:
    baseline_context = v2_arm_context(v2_documents, "baseline")
    candidate_context = v2_arm_context(v2_documents, "candidate")
    assert baseline_context["harness_identity_digest"] != candidate_context[
        "harness_identity_digest"
    ]

    item = target(analyze_goppi_trajectory_v2(*v2_documents), v2_documents)
    assert item["comparability"] == {
        "state": "comparable",
        "intervention": {
            "field": "context.harness_identity_digest",
            "state": "changed",
        },
        "held_constant": list(EXPECTED_V2_HELD_CONSTANT),
        "reasons": [],
    }


def test_execution_v2_reports_unchanged_arm_harness(v2_documents: Documents) -> None:
    baseline_context = v2_arm_context(v2_documents, "baseline")
    candidate_context = v2_arm_context(v2_documents, "candidate")
    candidate_context["harness_identity_digest"] = baseline_context["harness_identity_digest"]

    item = target(analyze_goppi_trajectory_v2(*v2_documents), v2_documents)
    assert item["comparability"]["state"] == "comparable"
    assert item["comparability"]["intervention"] == {
        "field": "context.harness_identity_digest",
        "state": "unchanged",
    }
    assert item["comparability"]["held_constant"] == list(EXPECTED_V2_HELD_CONSTANT)
    assert item["comparability"]["reasons"] == []


@pytest.mark.parametrize(
    ("value", "expected_state", "expected_reason"),
    [
        ("sha256:" + "a" * 64, "not_comparable", "different_context"),
        (None, "unknown", "missing_context"),
    ],
)
def test_execution_v2_invariant_budget_context_fails_closed(
    v2_documents: Documents,
    value: str | None,
    expected_state: str,
    expected_reason: str,
) -> None:
    v2_arm_context(v2_documents, "candidate")["budget_policy_digest"] = value

    item = target(analyze_goppi_trajectory_v2(*v2_documents), v2_documents)
    assert item["comparability"]["state"] == expected_state
    assert {
        "field": "context.budget_policy_digest",
        "reason": expected_reason,
    } in item["comparability"]["reasons"]
    assert "context.budget_policy_digest" not in item["comparability"]["held_constant"]
    assert item["comparability"]["intervention"] == {
        "field": "context.harness_identity_digest",
        "state": "changed",
    }


@pytest.mark.parametrize(
    "field",
    [
        "context.runner_model",
        "context.runner_profile",
        "context.verifier_runtime_digest",
        "context.budget_policy_digest",
        "context.declared_repeats",
        "verifier.verifier_id",
        "verifier.verifier_digest",
        "repeat_proof",
        "row.assignment_digest",
    ],
)
def test_execution_v2_invariant_differences_are_not_comparable(field: str) -> None:
    baseline = v2_comparison_side()
    candidate = v2_comparison_side()
    candidate["context"]["harness_identity_digest"] = "sha256:" + "b" * 64
    if "." in field:
        parent, child = field.split(".", maxsplit=1)
        if parent == "row":
            parent = "row_context"
        candidate[parent][child] = "different"
    else:
        candidate[field] = "different"

    comparability = v2_comparability_impl(baseline, candidate)
    assert comparability["state"] == "not_comparable"
    assert {"field": field, "reason": "different_context"} in comparability["reasons"]
    assert field not in comparability["held_constant"]
    assert comparability["intervention"]["state"] == "changed"


@pytest.mark.parametrize("field", ["context.budget_policy_digest", "row.assignment_digest"])
def test_execution_v2_missing_invariant_is_unknown(field: str) -> None:
    baseline = v2_comparison_side()
    candidate = v2_comparison_side()
    parent, child = field.split(".", maxsplit=1)
    if parent == "row":
        parent = "row_context"
    candidate[parent].pop(child)

    comparability = v2_comparability_impl(baseline, candidate)
    assert comparability["state"] == "unknown"
    assert {"field": field, "reason": "missing_context"} in comparability["reasons"]
    assert field not in comparability["held_constant"]


def test_execution_v2_missing_intervention_is_disclosed_as_unknown() -> None:
    baseline = v2_comparison_side()
    candidate = v2_comparison_side()
    candidate["context"].pop("harness_identity_digest")

    comparability = v2_comparability_impl(baseline, candidate)
    assert comparability["state"] == "unknown"
    assert comparability["intervention"] == {
        "field": "context.harness_identity_digest",
        "state": "unknown",
    }
    assert {
        "field": "context.harness_identity_digest",
        "reason": "missing_context",
    } in comparability["reasons"]


def test_execution_v2_repeat_count_is_independent_of_planned_case_count(
    v2_documents: Documents,
) -> None:
    documents = v2_one_case_two_repeats(v2_documents)

    report = cast(dict[str, Any], analyze_goppi_trajectory_v2(*documents))
    stage = next(stage for stage in report["stages"] if stage["stage"] == "same_batch")
    pairs = [item for item in report["comparisons"] if item["key"]["stage"] == "same_batch"]

    assert stage["planned_case_count"] == V2_SINGLE_CASE_COUNT
    assert stage["paired_rows"] == V2_SINGLE_CASE_TWO_REPEAT_ROWS
    assert stage["case_stage_repeat_pairs"] == V2_TWO_REPEAT_COUNT
    assert {item["key"]["repeat_index"] for item in pairs} == {0, 1}


@pytest.mark.parametrize("mutation", ["repeat_count_mismatch", "repeat_index_gap", "missing_case"])
def test_execution_v2_rejects_repeat_row_coverage_mismatch(
    v2_documents: Documents, mutation: str
) -> None:
    if mutation == "missing_case":
        documents = v2_missing_case_from_repeat(v2_documents)
    else:
        execution, rows, trajectory, identities = v2_one_case_two_repeats(v2_documents)
        documents = execution, rows, trajectory, identities
        if mutation == "repeat_count_mismatch":
            stage = next(stage for stage in execution["stages"] if stage["stage"] == "same_batch")
            stage["authenticated_repeat_count"] = 1
            execution["counts"]["authenticated_repeat_count_total"] = 1
            for unit in trajectory["units"]:
                unit["context"]["declared_repeats"] = 1
        else:
            for row in rows:
                if row["stage"] == "same_batch" and row["repeat_index"] == 1:
                    row["repeat_index"] = 2
            for unit in trajectory["units"]:
                if unit["unit"]["repeat_index"] == 1:
                    unit["unit"]["repeat_index"] = 2
                    for case in unit["cases"]:
                        case["row_key"]["repeat_index"] = 2

    with pytest.raises(TrajectoryError, match="TRAJECTORY_COVERAGE_MISMATCH"):
        analyze_goppi_trajectory_v2(*documents)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("arbitrary_repeat_run_id", "TRAJECTORY_BINDING_MISMATCH"),
        ("split_same_index_run_ids", "TRAJECTORY_COVERAGE_MISMATCH"),
        ("reused_repeat_run_id", "TRAJECTORY_COVERAGE_MISMATCH"),
        ("mixed_proof_families", "TRAJECTORY_COVERAGE_MISMATCH"),
        ("v1_proof_with_two_repeats", "TRAJECTORY_BINDING_MISMATCH"),
        ("v2_proof_with_one_repeat", "TRAJECTORY_BINDING_MISMATCH"),
        ("declared_repeat_mismatch", "TRAJECTORY_BINDING_MISMATCH"),
        ("roster_digest_mismatch", "TRAJECTORY_BINDING_MISMATCH"),
        ("roster_context_mismatch", "TRAJECTORY_BINDING_MISMATCH"),
        ("reordered_later_repeat_roster", "TRAJECTORY_BINDING_MISMATCH"),
        ("stage_run_id_mismatch", "TRAJECTORY_BINDING_MISMATCH"),
        ("partial_units_claim_full_roster", "TRAJECTORY_COVERAGE_MISMATCH"),
    ],
)
def test_execution_v2_rejects_repeat_identity_and_policy_attacks(
    v2_documents: Documents, mutation: str, reason: str
) -> None:
    attack_builders = {
        "arbitrary_repeat_run_id": v2_arbitrary_repeat_run_id,
        "split_same_index_run_ids": v2_split_repeat_index_run_ids,
        "reused_repeat_run_id": v2_reused_repeat_run_id,
        "mixed_proof_families": v2_mixed_repeat_proofs,
        "v1_proof_with_two_repeats": v2_primary_only_proof_with_two_repeats,
        "v2_proof_with_one_repeat": v2_single_repeat_with_attested_proof,
        "declared_repeat_mismatch": v2_declared_repeat_mismatch,
        "roster_digest_mismatch": v2_roster_digest_mismatch,
        "roster_context_mismatch": v2_roster_context_mismatch,
        "reordered_later_repeat_roster": v2_reordered_later_repeat_roster,
        "stage_run_id_mismatch": v2_stage_run_id_mismatch,
        "partial_units_claim_full_roster": v2_partial_units_claiming_full_roster,
    }
    documents = attack_builders[mutation](v2_documents)

    with pytest.raises(TrajectoryError) as caught:
        analyze_goppi_trajectory_v2(*documents)
    assert caught.value.reason == reason


def test_execution_v2_evaluated_stages_share_repeat_policy(v2_documents: Documents) -> None:
    documents = v2_one_case_two_repeats(v2_documents)
    report = cast(dict[str, Any], analyze_goppi_trajectory_v2(*documents))
    execution = documents[0]
    first_stage = execution["stages"][0]
    second_stage = copy.deepcopy(first_stage)
    second_stage.update(
        stage="held_in",
        split="confirm",
        stage_input_digest="sha256:" + "a" * 64,
        primary_inference_repeat_index=1,
    )
    first_stage_comparisons = [
        item
        for item in report["comparisons"]
        if item["key"]["stage"] == "same_batch"
    ]
    repeated_comparisons = [copy.deepcopy(item) for item in first_stage_comparisons]
    for comparison in repeated_comparisons:
        repeat_index = comparison["key"]["repeat_index"]
        comparison["key"].update(
            stage="held_in",
            split=second_stage["split"],
            stage_run_id=second_stage["stage_run_id"],
            repeat_run_id=v2_repeat_run_id_impl(
                execution["source"]["cycle_run_id"],
                second_stage["stage"],
                second_stage["stage_input_digest"],
                repeat_index,
            ),
        )
        for arm in ("baseline", "candidate"):
            comparison[arm]["primary_inference"] = repeat_index == 1

    with pytest.raises(TrajectoryError, match="TRAJECTORY_BINDING_MISMATCH"):
        v2_stage_parser_impl(
            {"source": execution["source"], "stages": [first_stage, second_stage]},
            [*first_stage_comparisons, *repeated_comparisons],
        )


def test_execution_v2_evaluated_stages_share_repeat_count_and_proof(
    v2_documents: Documents,
) -> None:
    documents = v2_one_case_two_repeats(v2_documents)
    report = cast(dict[str, Any], analyze_goppi_trajectory_v2(*documents))
    execution = documents[0]
    first_stage = execution["stages"][0]
    second_stage = copy.deepcopy(first_stage)
    second_stage.update(
        stage="held_in",
        split="confirm",
        authenticated_repeat_count=1,
        exported_row_count=2,
        primary_inference_repeat_index=0,
    )
    first_stage_comparisons = [
        item for item in report["comparisons"] if item["key"]["stage"] == "same_batch"
    ]
    repeated_comparison = copy.deepcopy(
        next(item for item in first_stage_comparisons if item["key"]["repeat_index"] == 0)
    )
    repeated_comparison["key"].update(
        stage="held_in",
        split="confirm",
        repeat_run_id=second_stage["stage_run_id"],
    )
    for arm in ("baseline", "candidate"):
        repeated_comparison[arm]["primary_inference"] = True
        repeated_comparison[arm]["repeat_proof"] = "v1_primary_only"

    with pytest.raises(TrajectoryError, match="TRAJECTORY_BINDING_MISMATCH"):
        v2_stage_parser_impl(
            {"source": execution["source"], "stages": [first_stage, second_stage]},
            [*first_stage_comparisons, repeated_comparison],
        )


def test_execution_v2_unavailable_build_is_not_measured(v2_documents: Documents) -> None:
    execution, rows, trajectory, identities = v2_documents
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
    report = analyze_goppi_trajectory(execution, rows, trajectory, identities)
    declarations = cast(dict[str, Any], report["producer_declarations"])
    builds = cast(dict[str, Any], declarations["builds"])
    assert builds["export"]["state"] == "not_measured"
    assert builds["export"]["unavailable_reason"] == "package_unverifiable"
    assert builds["stages"][0]["record"]["state"] == "not_measured"


@pytest.mark.parametrize("status", ["abstained", "indeterminate", "not_measured"])
def test_execution_v2_does_not_promote_unknown_row_outcomes(
    v2_documents: Documents, status: str
) -> None:
    v2_documents[1][0]["result"]["status"] = status
    with pytest.raises(TrajectoryError, match="TRAJECTORY_UNSUPPORTED_VALUE"):
        analyze_goppi_trajectory(*v2_documents)


@pytest.mark.parametrize(
    "mutation",
    [
        "execution_family",
        "trajectory_family",
        "row_family",
        "missing_stage",
        "duplicate_stage",
        "unexpected_execution_field",
    ],
)
def test_execution_v2_refuses_family_mixing_and_open_shapes(
    v2_documents: Documents, mutation: str
) -> None:
    execution, rows, trajectory, _ = v2_documents
    if mutation == "execution_family":
        execution["schema"] = "goppi.paired_run_exchange.execution.v1"
    elif mutation == "trajectory_family":
        trajectory["schema"] = "goppi.paired_trajectory_exchange.document.v1"
    elif mutation == "row_family":
        rows[0]["schema"] = "goppi.paired_run_exchange.row.v1"
    elif mutation == "missing_stage":
        execution["stages"].pop()
    elif mutation == "duplicate_stage":
        execution["stages"][1]["stage"] = execution["stages"][0]["stage"]
    else:
        execution["unrecognized"] = True
    with pytest.raises(TrajectoryError):
        analyze_goppi_trajectory(execution, rows, trajectory, v2_documents[3])


@pytest.mark.parametrize(
    ("entrypoint", "schema"),
    [
        ("v2", "goppi.paired_run_exchange.execution.v1"),
        ("dispatcher", "goppi.paired_run_exchange.execution.v3"),
    ],
)
def test_execution_family_refusal_keeps_unsupported_format_diagnostic(
    v2_documents: Documents, entrypoint: str, schema: str
) -> None:
    execution, rows, trajectory, identities = v2_documents
    execution["schema"] = schema
    with pytest.raises(TrajectoryError) as caught:
        if entrypoint == "v2":
            analyze_goppi_trajectory_v2(execution, rows, trajectory, identities)
        else:
            analyze_goppi_trajectory(execution, rows, trajectory, identities)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "UNSUPPORTED_FORMAT",
        "TRAJECTORY_UNSUPPORTED_VALUE",
        "trajectory",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approval_created_by_exporter", True),
        ("independently_regraded", True),
        ("local_result_authentication_checked", False),
        ("model_contacted_by_exporter", True),
        ("network_contacted_by_exporter", True),
        ("source_project_modified_by_exporter", True),
        ("subordinate_content_and_crosslinks_checked", False),
    ],
)
def test_execution_v2_refuses_changed_fixed_claims(
    v2_documents: Documents, field: str, value: bool
) -> None:
    v2_documents[0]["claims"][field] = value
    with pytest.raises(TrajectoryError, match="TRAJECTORY_BINDING_MISMATCH"):
        analyze_goppi_trajectory(*v2_documents)


@pytest.mark.parametrize(
    "field", ["execution_attestation_digest", "measurement_receipt_digest"]
)
def test_execution_v2_requires_evaluated_stage_receipt_digests(
    v2_documents: Documents, field: str
) -> None:
    v2_documents[0]["stages"][0][field] = None
    with pytest.raises(TrajectoryError):
        analyze_goppi_trajectory(*v2_documents)


@pytest.mark.parametrize("field", ["paired_manifest_digest", "paired_manifest_sha256"])
def test_execution_v2_requires_trajectory_manifest_digest_fields(
    v2_documents: Documents, field: str
) -> None:
    v2_documents[2][field] = "invalid"
    with pytest.raises(TrajectoryError):
        analyze_goppi_trajectory(*v2_documents)


@pytest.mark.parametrize(
    "field", ["projection_binding_checked", "source_authentication_checked"]
)
def test_execution_v2_requires_positive_trajectory_crosslink_claims(
    v2_documents: Documents, field: str
) -> None:
    v2_documents[2]["producer_claims"][field] = False
    with pytest.raises(TrajectoryError, match="TRAJECTORY_BINDING_MISMATCH"):
        analyze_goppi_trajectory(*v2_documents)


def test_execution_v2_rejects_every_pinned_producer_refusal(v2_documents: Documents) -> None:
    fixture_root = V2_FIXTURE / "paired-exchange-v2"
    rejection_root = V2_FIXTURE / "paired-exchange-v2-rejections"
    base = {path: (fixture_root / path).read_bytes() for path in MEMBERS}
    cases = sorted(path for path in rejection_root.iterdir() if path.is_dir())
    assert len(cases) == V2_REFUSAL_FIXTURES
    for case in cases:
        changed = dict(base)
        for replacement in case.rglob("*"):
            if replacement.is_file():
                relative = replacement.relative_to(case).as_posix()
                changed[relative] = replacement.read_bytes()
        execution = json.loads(changed["paired/execution.json"])
        rows = tuple(json.loads(line) for line in changed["paired/rows.jsonl"].splitlines())
        trajectory = json.loads(changed["trajectory/trajectory.json"])
        identities = {
            path: ("sha256:" + hashlib.sha256(raw).hexdigest(), len(raw))
            for path, raw in changed.items()
        }
        with pytest.raises(TrajectoryError):
            analyze_goppi_trajectory(execution, rows, trajectory, identities)


def test_pi15_trajectory_keeps_descriptive_and_authority_boundaries(documents: Documents) -> None:
    report = analyze(pi15_documents(documents))
    assert report["state"] == "analyzed"
    assert report["populations"]["paired_rows"] == len(documents[1])
    assert report["authority"]["execution_authorized"] is False
    assert "call_relationships_not_captured" in report["interpretation_limits"]


@pytest.mark.parametrize("mismatch", ["runner", "capture", "context"])
def test_pi15_profile_binding_refuses_mismatch(documents: Documents, mismatch: str) -> None:
    execution, _, trajectory, _ = pi15_documents(documents)
    if mismatch == "runner":
        execution["source"]["runner"]["profile"] = "pi_1_3_0"
    elif mismatch == "capture":
        trajectory["units"][0]["capture_profile"] = "native_pi_1_3_lifecycle.v1"
    else:
        trajectory["units"][0]["context"]["runner_profile"] = "pi_1_3_0"
    with pytest.raises(TrajectoryError, match="TRAJECTORY_BINDING_MISMATCH"):
        analyze(documents)


def test_unrecognized_new_runner_profile_is_unsupported(documents: Documents) -> None:
    documents[0]["source"]["runner"]["profile"] = "pi_1_6_0"
    with pytest.raises(TrajectoryError, match="TRAJECTORY_UNSUPPORTED_VALUE"):
        analyze(documents)


@pytest.mark.parametrize("mismatch", ["capture", "null_context"])
def test_pi15_unavailable_unit_still_binds_capture_profile(
    documents: Documents, mismatch: str
) -> None:
    _, _, trajectory, _ = pi15_documents(documents)
    unit = trajectory["units"][0]
    unit.update(
        source_availability="interrupted",
        unavailable_reason="pi_lifecycle_interrupted",
        capture_completeness="incomplete",
    )
    for case in unit["cases"]:
        case.update(spans=[], verifier_observation=None)
    count = len(unit["cases"])
    trajectory["availability_counts"]["retained"] -= count
    trajectory["availability_counts"]["interrupted"] += count
    assert analyze(documents)["capture_counts"]["interrupted"] == count
    if mismatch == "capture":
        unit["capture_profile"] = "native_pi_1_3_lifecycle.v1"
    else:
        unit["context"] = None
    with pytest.raises(TrajectoryError, match="TRAJECTORY_BINDING_MISMATCH"):
        analyze(documents)


def test_outcome_actions_selects_exactly_comparable_observed_changed_pairs(
    documents: Documents,
) -> None:
    from underwrite.instrument.evidence.goppi_trajectory import (
        OUTCOME_ACTIONS_SCHEMA,
        analyze_goppi_trajectory_actions,
    )

    report = analyze_goppi_trajectory_actions(*documents)
    comparisons = cast(list[dict[str, Any]], report["comparisons"])
    verification_tasks = cast(list[dict[str, Any]], report["next_verification_tasks"])
    assert report["schema"] == OUTCOME_ACTIONS_SCHEMA == "goppi_trajectory_outcome_actions.v1"
    assert report["policy"] == "goppi.descriptive-paired-trajectory-outcome-actions.v1"
    assert report["authority"] == analyze(documents)["authority"]
    assert report["trust_scope"] == analyze(documents)["trust_scope"]
    assert report["interpretation_limits"] == analyze(documents)["interpretation_limits"]
    tasks = [
        task
        for task in verification_tasks
        if task["kind"] == "inspect_outcome_change_without_tool_status_change"
    ]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["owner"] == "calling_agent"
    assert task["execution_authorized"] is False
    assert "command" not in task
    expected = {
        comparison["id"]
        for comparison in comparisons
        if comparison["comparability"]["state"] == "comparable"
        and comparison["difference"]["state"] == "observed"
        and comparison["difference"]["outcome_changed"] is True
        and comparison["difference"]["tool_status_deltas"] == []
    }
    assert len(expected) == EXPECTED_OUTCOME_ACTION_SCOPES
    assert {scope["comparison_id"] for scope in task["scope"]} == expected
    assert all(scope["arms"] == ["baseline", "candidate"] for scope in task["scope"])
    assert len(task["scope"]) == len(expected)
    assert any(
        item["difference"]["outcome_changed"] is True
        and item["difference"]["tool_status_deltas"]
        and item["id"] not in expected
        for item in comparisons
    )
    assert any(
        item["difference"]["outcome_changed"] is False
        and item["difference"]["tool_status_deltas"] == []
        and item["id"] not in expected
        for item in comparisons
    )


def test_outcome_actions_preserves_existing_tasks_and_order_independence(
    documents: Documents,
) -> None:
    from underwrite.instrument.evidence.goppi_trajectory import analyze_goppi_trajectory_actions

    ordinary = analyze(documents)
    actions = analyze_goppi_trajectory_actions(*documents)
    verification_tasks = cast(list[dict[str, Any]], actions["next_verification_tasks"])
    existing = [
        task
        for task in verification_tasks
        if task["kind"] != "inspect_outcome_change_without_tool_status_change"
    ]
    assert existing == ordinary["next_verification_tasks"]
    execution, rows, trajectory, identities = documents
    trajectory["units"].reverse()
    for unit in trajectory["units"]:
        unit["cases"].reverse()
    reordered = analyze_goppi_trajectory_actions(
        execution, tuple(reversed(rows)), trajectory, identities
    )
    assert reordered["next_verification_tasks"] == actions["next_verification_tasks"]


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
    assert item["id"] not in task_scopes(report, "inspect_tool_status_difference")
    assert item["id"] not in task_scopes(report, "obtain_call_relationships")


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


@pytest.mark.parametrize(
    "runner_model,expected_state,expected_reason",
    [
        ("other-model", "not_comparable", "different_context"),
        (None, "unknown", "missing_context"),
    ],
)
def test_tool_delta_tasks_respect_comparability(
    documents: Documents, runner_model: str | None, expected_state: str, expected_reason: str
) -> None:
    baseline = analyze(documents)
    comparable = target(baseline, documents)
    assert comparable["comparability"]["state"] == "comparable"
    assert comparable["difference"]["tool_status_deltas"]
    assert comparable["id"] in task_scopes(baseline, "inspect_tool_status_difference")
    assert comparable["id"] in task_scopes(baseline, "obtain_call_relationships")
    assert comparable["id"] not in task_scopes(baseline, "check_outcome_comparability")

    context = documents[2]["units"][0]["context"]
    if runner_model is None:
        context.pop("runner_model")
    else:
        context["runner_model"] = runner_model
    report = analyze(documents)
    item = target(report, documents)
    assert item["comparability"]["state"] == expected_state
    assert {"field": "context.runner_model", "reason": expected_reason} in item["comparability"][
        "reasons"
    ]
    assert item["difference"] == comparable["difference"]
    assert task_scopes(report, "check_outcome_comparability")[item["id"]] == [
        "baseline",
        "candidate",
    ]
    assert task_scopes(report, "inspect_tool_status_difference")[item["id"]] == [
        "baseline",
        "candidate",
    ]
    assert item["id"] not in task_scopes(report, "obtain_call_relationships")


def test_observed_no_delta_adds_no_tool_tasks(documents: Documents) -> None:
    report = analyze(documents)
    item = next(
        comparison
        for comparison in report["comparisons"]
        if comparison["comparability"]["state"] == "comparable"
        and comparison["difference"]["state"] == "observed"
        and not comparison["difference"]["tool_status_deltas"]
    )
    assert item["id"] not in task_scopes(report, "check_outcome_comparability")
    assert item["id"] not in task_scopes(report, "inspect_tool_status_difference")
    assert item["id"] not in task_scopes(report, "obtain_call_relationships")


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
