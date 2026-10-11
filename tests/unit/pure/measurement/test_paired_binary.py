"""The primary cohort, missingness and hard work limits remain explicit."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, cast

import pytest

from underwrite.instrument.evidence.paired_binary import (
    PairedBinaryError,
    evaluate_paired_binary,
    evaluate_paired_binary_with_groups,
)


def _evaluate(value: object, settings: object) -> dict[str, Any]:
    return cast(dict[str, Any], evaluate_paired_binary(value, settings))


def _evaluate_groups(
    value: object, settings: object, groups: object
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report, group_reports = evaluate_paired_binary_with_groups(value, settings, groups)
    return cast(dict[str, Any], report), cast(list[dict[str, Any]], group_reports)


def group(category: str | None, *case_ids: str) -> dict[str, object]:
    return {"category": category, "case_ids": list(case_ids)}


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


def test_primary_transitions_preserve_all_four_cells_and_effect() -> None:
    value = source_input(("a", "b", "c", "d"))
    pairs = {"a": (0, 0), "b": (0, 1), "c": (1, 0), "d": (1, 1)}
    for record in value["slot_records"]:
        record["outcome"] = pairs[record["case_id"]][record["arm"] == "candidate"]
    result = _evaluate(value, policy())
    transitions = result["accounting"]["primary_transitions"]
    assert transitions == {
        "scope": "primary_repeat_zero",
        "complete_pair_count": len(pairs),
        "incomplete_pair_count": 0,
        "zero_to_zero": 1,
        "zero_to_one": 1,
        "one_to_zero": 1,
        "one_to_one": 1,
    }
    assert (
        result["estimate"]["effect"]
        == (transitions["zero_to_one"] - transitions["one_to_zero"])
        / transitions["complete_pair_count"]
    )


@pytest.mark.parametrize("absence", ["infra", "verifier", "not_started", "unknown", "unreported"])
def test_partial_transitions_never_impute_missing_or_use_extra_repeats(absence: str) -> None:
    value = source_input()
    missing = value["slot_records"].pop(1)
    if absence != "unreported":
        value["slot_records"].append(
            {key: val for key, val in missing.items() if key != "outcome"}
            | {"missing_reason": absence}
        )
    for arm in ("baseline", "candidate"):
        slot = {"case_id": "a", "arm": arm, "repeat": 1}
        value["planned_slots"].append(slot)
        value["slot_records"].append({**slot, "outcome": 1})
    result = _evaluate(value, policy())
    assert result["accounting"]["primary_transitions"] == {
        "scope": "primary_repeat_zero",
        "complete_pair_count": 1,
        "incomplete_pair_count": 1,
        "zero_to_zero": 1,
        "zero_to_one": 0,
        "one_to_zero": 0,
        "one_to_one": 0,
    }
    assert result["status"] == "not_measured"
    assert all(result["estimate"][key] is None for key in ("n", "effect", "interval", "p_value"))


def test_transition_inventory_and_assumptions_do_not_invent_inference() -> None:
    value = source_input()
    expected = _evaluate(value, policy())["accounting"]["primary_transitions"]
    del value["planned_slots"]
    result = _evaluate(value, policy())
    assert result["accounting"]["primary_transitions"] == expected
    assert result["reason"] == "PLANNED_SLOT_INVENTORY_MISSING"
    del value["primary_case_ids"]
    assert _evaluate(value, policy())["accounting"]["primary_transitions"] is None
    settings = policy()
    settings["assumptions"]["independent_units"] = False
    result = _evaluate(source_input(), settings)
    assert result["accounting"]["primary_transitions"] == expected
    assert result["reason"] == "ASSUMPTIONS_NOT_DECLARED"


@pytest.mark.parametrize("cases", [(), ("a",)])
def test_small_transition_cohorts_remain_descriptive(cases: tuple[str, ...]) -> None:
    result = _evaluate(source_input(cases), policy())
    transitions = result["accounting"]["primary_transitions"]
    assert transitions["complete_pair_count"] == len(cases)
    assert transitions["incomplete_pair_count"] == 0
    assert sum(
        transitions[key] for key in ("zero_to_zero", "zero_to_one", "one_to_zero", "one_to_one")
    ) == len(cases)
    assert result["status"] == "not_measured"


@pytest.mark.parametrize(
    "variant", ["complete", "incomplete", "no_plan", "false_assumption", "empty"]
)
def test_group_evaluation_preserves_existing_report_exactly(variant: str) -> None:
    cases = () if variant == "empty" else ("a", "b")
    value = source_input(cases)
    settings = policy()
    if variant == "incomplete":
        value["slot_records"].pop()
    elif variant == "no_plan":
        del value["planned_slots"]
    elif variant == "false_assumption":
        settings["assumptions"]["fixed_sample"] = False
    groups = [group("known", *cases), group(None)]
    expected = _evaluate(value, settings)
    actual, _ = _evaluate_groups(value, settings, groups)
    assert actual == expected
    assert canonical_json(actual) == canonical_json(expected)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_group_counts_partition_primary_accounting_without_pooling_repeats() -> None:
    value = source_input(("a", "b", "c", "d"))
    pairs = {"a": (0, 0), "b": (0, 1), "c": (1, 0), "d": (1, 1)}
    for record in value["slot_records"]:
        record["outcome"] = pairs[record["case_id"]][record["arm"] == "candidate"]
    value["slot_records"][3] = {
        "case_id": "b",
        "arm": "candidate",
        "repeat": 0,
        "missing_reason": "verifier",
    }
    repeat = {"case_id": "a", "arm": "candidate", "repeat": 1}
    value["planned_slots"].append(repeat)
    value["slot_records"].append({**repeat, "outcome": 1})
    report, groups = _evaluate_groups(
        value,
        policy(),
        [group(None, "d"), group("zeta", "c"), group("alpha", "a", "b")],
    )
    assert [group["category"] for group in groups] == ["alpha", "zeta", None]
    assert [group["case_count"] for group in groups] == [2, 1, 1]
    overall = report["accounting"]["primary_repeat_zero"]
    for field in ("planned", "reported", "observed_one", "observed_zero", "unreported_planned"):
        assert (
            sum(group[arm][field] for group in groups for arm in ("baseline", "candidate"))
            == overall[field]
        )
    for reason in ("infra", "verifier", "not_started", "unknown"):
        assert (
            sum(
                group[arm]["missing_by_reason"][reason]
                for group in groups
                for arm in ("baseline", "candidate")
            )
            == overall["missing_by_reason"][reason]
        )
    transition_fields = (
        "complete_pair_count",
        "incomplete_pair_count",
        "zero_to_zero",
        "zero_to_one",
        "one_to_zero",
        "one_to_one",
    )
    overall_transitions = report["accounting"]["primary_transitions"]
    for field in transition_fields:
        assert sum(group["transitions"][field] for group in groups) == overall_transitions[field]
    assert groups[0]["candidate"]["missing_by_reason"]["verifier"] == 1
    assert groups[0]["candidate"]["observed_zero"] == 1
    assert report["accounting"]["additional_repeats"]["observed_one"] == 1
    assert sum(group["candidate"]["reported"] for group in groups) == len(pairs)


def test_groups_keep_unknown_plan_and_empty_declared_categories() -> None:
    value = source_input(("a",))
    del value["planned_slots"]
    _, groups = _evaluate_groups(
        value,
        policy(),
        [group("empty"), group(None, "a")],
    )
    assert groups[0]["case_count"] == 0
    assert groups[0]["baseline"]["planned"] is None
    assert groups[0]["baseline"]["unreported_planned"] is None
    assert groups[0]["transitions"]["complete_pair_count"] == 0
    assert groups[1]["baseline"]["planned"] is None


@pytest.mark.parametrize(
    "groups",
    [
        [],
        [group("only", "a", "b")],
        [group(None, "a"), group(None, "b")],
        [group(None)],
        [group(None, "a", "b", "other")],
        [
            group("duplicate member", "a"),
            group(None, "a", "b"),
        ],
        [
            group("duplicate", "a"),
            group("duplicate", "b"),
            group(None),
        ],
        [group("e\u0301", "a", "b"), group(None)],
        [
            {**group("blank", "a", "b"), "extra": 1},
            group(None),
        ],
    ],
)
def test_group_binding_rejects_missing_unknown_duplicate_or_malformed_membership(
    groups: object,
) -> None:
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate_groups(source_input(), policy(), groups)
    assert exc.value.code == "INPUT_BINDING_MISMATCH"


def test_group_bounds_and_missing_primary_are_binding_mismatches() -> None:
    too_many: list[dict[str, object]] = [group(f"group-{index}") for index in range(33)]
    too_many.append(group(None, "a", "b"))
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate_groups(source_input(), policy(), too_many)
    assert exc.value.code == "INPUT_BINDING_MISMATCH"
    value = source_input()
    del value["primary_case_ids"]
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate_groups(value, policy(), [group(None)])
    assert exc.value.code == "INPUT_BINDING_MISMATCH"


def test_invalid_group_binding_is_rejected_before_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(_parsed: object) -> object:
        raise AssertionError("measurement must not run before group binding validation")

    monkeypatch.setattr(
        "underwrite.instrument.evidence.paired_binary._measure",
        fail_if_called,
    )
    with pytest.raises(PairedBinaryError) as exc:
        _evaluate_groups(
            source_input(),
            policy(),
            [group("partial", "a"), group(None)],
        )
    assert exc.value.code == "INPUT_BINDING_MISMATCH"
