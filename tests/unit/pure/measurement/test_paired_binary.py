"""The primary cohort, missingness and hard work limits remain explicit."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, cast

import pytest

from underwrite.instrument.evidence.paired_binary import PairedBinaryError, evaluate_paired_binary


def _evaluate(value: object, settings: object) -> dict[str, Any]:
    return cast(dict[str, Any], evaluate_paired_binary(value, settings))


PAIRED_COUNT = 2
EXPECTED_EFFECT = 0.5
THREE_OBSERVED_ZEROES = 3
ANNOTATED_REPEAT_COUNT = 17
EXAMPLE_LIMIT = 16


def source_input(cases: tuple[str, ...] = ("a", "b")) -> dict[str, Any]:
    planned = [
        {"case_id": case, "arm": arm, "repeat": 0}
        for case in cases
        for arm in ("baseline", "candidate")
    ]
    records = [
        {**slot, "outcome": int(slot["case_id"] == "a" and slot["arm"] == "candidate")}
        for slot in planned
    ]
    return {
        "schema": "paired_binary_input.v1",
        "source": {"producer": "test", "version": "1", "run_id": "one"},
        "outcome_definition": {"name": "resolved", "positive_means": "case resolved"},
        "metadata": {
            "baseline_context_id": "baseline-fixture",
            "candidate_context_id": "candidate-fixture",
            "evaluator_id": "binary-fixture",
        },
        "primary_case_ids": list(cases),
        "planned_slots": planned,
        "slot_records": records,
    }


def policy() -> dict[str, Any]:
    return {
        "schema": "paired_binary_policy.v1",
        "seed": 7,
        "permutation_iterations": 10_000,
        "bootstrap_iterations": 1_000,
        "confidence": 0.95,
        "max_work": 2_000_000,
        "assumptions": {
            "independent_units": True,
            "sign_flip_invariance": True,
            "fixed_sample": True,
        },
    }


def test_complete_primary_cohort_measures_only_declared_binary_pairs() -> None:
    result = _evaluate(source_input(), policy())
    assert result["status"] == "measured" and result["reason"] is None
    estimate = result["estimate"]
    assert isinstance(estimate, dict)
    assert estimate["n"] == PAIRED_COUNT and estimate["effect"] == EXPECTED_EFFECT
    assert estimate["scope"] == "primary_repeat_zero"
    assert estimate["effect_method"] == "mean-paired-difference-v1"
    assert estimate["bootstrap_method"] == "percentile-linear-v1"
    assert estimate["interval"] is not None and estimate["p_value"] is not None
    accounting = result["accounting"]
    assert isinstance(accounting, dict)
    assert accounting["primary_repeat_zero"]["observed_zero"] == THREE_OBSERVED_ZEROES
    assert result["source_claims"]["verified"] is False
    assert result["assumptions"]["verified"] is False
    assert "observed_reason_examples" not in accounting
    assert "observed_reason_examples_omitted" not in accounting


def test_observed_reasons_preserve_zero_and_one_without_changing_estimate() -> None:
    original = source_input()
    annotated = deepcopy(original)
    annotated["slot_records"][0]["source_reason"] = "ERROR-like text is still observed zero"
    annotated["slot_records"][1]["source_reason"] = "source says completed"
    before = _evaluate(original, policy())
    after = _evaluate(annotated, policy())
    assert before["estimate"] == after["estimate"]
    assert before["accounting"]["primary_repeat_zero"] == after["accounting"]["primary_repeat_zero"]
    assert after["accounting"]["observed_reason_examples"] == [
        {
            "case_id": "a",
            "arm": "baseline",
            "repeat": 0,
            "outcome": 0,
            "source_reason": "ERROR-like text is still observed zero",
            "source_claim_unverified": True,
        },
        {
            "case_id": "a",
            "arm": "candidate",
            "repeat": 0,
            "outcome": 1,
            "source_reason": "source says completed",
            "source_claim_unverified": True,
        },
    ]
    assert after["accounting"]["observed_reason_examples_omitted"] == 0


def test_observed_repeat_reasons_are_bounded_sorted_and_not_pooled() -> None:
    value = source_input()
    for repeat in reversed(range(1, 18)):
        slot = {"case_id": "a", "arm": "candidate", "repeat": repeat}
        value["planned_slots"].append(slot)
        value["slot_records"].append({**slot, "outcome": 1, "source_reason": f"repeat {repeat}"})
    result = _evaluate(value, policy())
    examples = result["accounting"]["observed_reason_examples"]
    assert result["estimate"]["n"] == PAIRED_COUNT
    assert result["accounting"]["additional_repeats"]["observed_one"] == ANNOTATED_REPEAT_COUNT
    assert len(examples) == EXAMPLE_LIMIT
    assert examples[0]["repeat"] == 1 and examples[-1]["repeat"] == EXAMPLE_LIMIT
    assert result["accounting"]["observed_reason_examples_omitted"] == 1
    assert "SOURCE_REASON_EXAMPLES_TRUNCATED" in result["limitations"]


@pytest.mark.parametrize("reason", [7, " \t ", "x" * 129, "e\u0301"])
def test_invalid_observed_source_reason_is_rejected(reason: object) -> None:
    value = source_input()
    value["slot_records"][0]["source_reason"] = reason
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate(value, policy())
    assert exc.value.code == "INVALID_INPUT"


def test_multiline_missing_and_observed_reasons_remain_escaped_source_data() -> None:
    value = source_input()
    value["slot_records"][0] = {
        "case_id": "a",
        "arm": "baseline",
        "repeat": 0,
        "missing_reason": "infra",
        "source_reason": "source\ncontrol",
    }
    missing = _evaluate(value, policy())
    assert missing["status"] == "not_measured"
    assert missing["accounting"]["missing_examples"][0]["source_reason"] == "source\ncontrol"
    value = source_input()
    value["slot_records"][0]["source_reason"] = "source\ncontrol"
    observed = _evaluate(value, policy())
    assert observed["status"] == "measured"
    assert (
        observed["accounting"]["observed_reason_examples"][0]["source_reason"] == "source\ncontrol"
    )
    assert "source\\ncontrol" in json.dumps(observed)


def test_observed_outcome_and_missing_reason_remain_exclusive() -> None:
    value = source_input()
    value["slot_records"][0]["missing_reason"] = "infra"
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate(value, policy())
    assert exc.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("cases", [(), ("a",)])
def test_zero_or_one_case_never_reports_numeric_measurement(cases: tuple[str, ...]) -> None:
    result = _evaluate(source_input(cases), policy())
    assert result["status"] == "not_measured"
    estimate = result["estimate"]
    assert isinstance(estimate, dict)
    assert all(estimate[key] is None for key in ("n", "effect", "interval", "p_value"))


def test_missing_inventory_is_unknown_even_with_seen_records() -> None:
    value = source_input()
    del value["planned_slots"]
    result = _evaluate(value, policy())
    assert result["reason"] == "PLANNED_SLOT_INVENTORY_MISSING"
    assert result["accounting"]["planned_slot_count"] is None
    value["planned_slots"] = source_input()["planned_slots"]
    del value["primary_case_ids"]
    result = _evaluate(value, policy())
    assert result["reason"] == "PRIMARY_CASE_INVENTORY_MISSING"


def test_context_identity_is_declared_but_not_verified() -> None:
    value = source_input()
    del value["metadata"]["candidate_context_id"]
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate(value, policy())
    assert exc.value.code == "INVALID_INPUT"
    value = source_input()
    result = _evaluate(value, policy())
    assert result["source_claims"]["metadata"]["candidate_context_id"] == "candidate-fixture"
    assert result["source_claims"]["verified"] is False


@pytest.mark.parametrize("field", ["source_run_id", "evaluator_id"])
def test_blank_required_identity_never_produces_measurement(field: str) -> None:
    value = source_input()
    if field == "source_run_id":
        value["source"]["run_id"] = " \t "
    else:
        value["metadata"]["evaluator_id"] = " \t "
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate(value, policy())
    assert exc.value.code == "INVALID_INPUT"


def test_infra_missing_is_distinct_from_observed_zero() -> None:
    value = source_input()
    value["slot_records"][1] = {
        "case_id": "a",
        "arm": "candidate",
        "repeat": 0,
        "missing_reason": "infra",
        "source_reason": "fixture says container unavailable",
    }
    result = _evaluate(value, policy())
    assert result["status"] == "not_measured"
    assert result["reason"] == "PRIMARY_COHORT_INCOMPLETE"
    primary = result["accounting"]["primary_repeat_zero"]
    assert (
        primary["observed_zero"] == THREE_OBSERVED_ZEROES
        and primary["missing_by_reason"]["infra"] == 1
    )
    assert result["accounting"]["missing_examples"][0]["source_claim_unverified"] is True


def test_unreported_primary_slot_is_unknown_not_a_zero() -> None:
    value = source_input()
    value["slot_records"].pop()
    result = _evaluate(value, policy())
    assert result["status"] == "not_measured"
    assert result["accounting"]["primary_repeat_zero"]["unreported_planned"] == 1


def test_additional_repeats_are_accounted_without_inflating_primary_n() -> None:
    value = source_input()
    value["planned_slots"].append({"case_id": "a", "arm": "candidate", "repeat": 1})
    value["slot_records"].append(
        {"case_id": "a", "arm": "candidate", "repeat": 1, "missing_reason": "not_started"}
    )
    result = _evaluate(value, policy())
    assert result["status"] == "measured" and result["estimate"]["n"] == PAIRED_COUNT
    assert result["accounting"]["additional_repeats"]["missing_by_reason"]["not_started"] == 1
    assert "ADDITIONAL_REPEATS_NOT_INCLUDED_IN_ESTIMATE" in result["limitations"]


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("duplicate_planned", "DUPLICATE_SLOT"),
        ("duplicate_record", "DUPLICATE_SLOT"),
        ("unplanned_record", "UNPLANNED_SLOT"),
        ("unknown_case", "UNKNOWN_CASE_ID"),
        ("repeat_over_limit", "INVALID_INPUT"),
        ("boolean_outcome", "INVALID_INPUT"),
        ("nan_outcome", "INVALID_INPUT"),
    ],
)
def test_slot_contract_rejects_ambiguous_or_unbounded_data(mutation: str, code: str) -> None:
    value = source_input()
    if mutation == "duplicate_planned":
        value["planned_slots"].append(deepcopy(value["planned_slots"][0]))
    elif mutation == "duplicate_record":
        value["slot_records"].append(deepcopy(value["slot_records"][0]))
    elif mutation == "unplanned_record":
        value["slot_records"].append({"case_id": "a", "arm": "baseline", "repeat": 1, "outcome": 1})
    elif mutation == "unknown_case":
        value["planned_slots"].append({"case_id": "other", "arm": "baseline", "repeat": 1})
    elif mutation == "repeat_over_limit":
        value["planned_slots"][0]["repeat"] = 1025
    elif mutation == "boolean_outcome":
        value["slot_records"][0]["outcome"] = True
    else:
        value["slot_records"][0]["outcome"] = float("nan")
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate(value, policy())
    assert exc.value.code == code


def test_assumptions_false_and_work_limit_remain_nonverdicts() -> None:
    settings = policy()
    settings["assumptions"]["fixed_sample"] = False
    result = _evaluate(source_input(), settings)
    assert result["reason"] == "ASSUMPTIONS_NOT_DECLARED"
    assert result["estimate"]["p_value"] is None
    settings = policy()
    settings["max_work"] = 1
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate(source_input(), settings)
    assert exc.value.code == "COMPUTATION_LIMIT_EXCEEDED"
    settings = policy()
    settings["permutation_iterations"] = 20_001
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate(source_input(), settings)
    assert exc.value.code == "INVALID_INPUT"


def test_slot_array_order_does_not_change_accounting_or_estimate() -> None:
    original = source_input()
    reversed_input = deepcopy(original)
    reversed_input["primary_case_ids"].reverse()
    reversed_input["planned_slots"].reverse()
    reversed_input["slot_records"].reverse()
    first = _evaluate(original, policy())
    second = _evaluate(reversed_input, policy())
    assert first["accounting"] == second["accounting"]
    assert first["estimate"] == second["estimate"]
    assert first["input_digest"] != second["input_digest"]


def test_combined_slot_cap_is_enforced_before_measurement() -> None:
    value = source_input()
    for repeat in range(1, 513):
        for case_id in ("a", "b"):
            for arm in ("baseline", "candidate"):
                slot = {"case_id": case_id, "arm": arm, "repeat": repeat}
                value["planned_slots"].append(slot)
                value["slot_records"].append({**slot, "outcome": 0})
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate(value, policy())
    assert exc.value.code == "INPUT_LIMIT_EXCEEDED"
