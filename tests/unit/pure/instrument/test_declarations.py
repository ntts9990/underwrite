"""Declared equality is narrower than observed execution or semantic equivalence."""

from __future__ import annotations

from copy import deepcopy

import pytest

from underwrite.instrument.evidence.declarations import DeclarationError, compare_declarations


def _conditions() -> dict[str, object]:
    return {
        "suite": "suite-a",
        "prompt_version": "prompt-v1",
        "model": "model-a",
        "gen_params": {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 128,
            "think": "off",
            "seed": 7,
            "num_ctx": 2048,
            "timeout_s": 30.0,
        },
    }


def _metric() -> dict[str, object]:
    return {
        "name": "accuracy",
        "unit": "fraction",
        "higher_is_better": True,
        "scope": "case",
        "denominator": "valid-evaluations",
        "aggregation": "mean",
        "evaluator": "exact-match",
        "evaluator_version": "v1",
        "policy": "policy-a",
        "threshold": 0.0,
    }


def test_complete_matching_declarations_only_get_a_declared_same_relation() -> None:
    comparison = compare_declarations(_conditions(), _conditions(), _metric(), _metric())
    assert comparison == {"declared_relation": "same", "differences": [], "unknowns": []}


def test_known_difference_survives_missing_and_null_fields() -> None:
    baseline, candidate = _conditions(), _conditions()
    candidate["model"] = "model-b"
    candidate_gen = deepcopy(candidate["gen_params"])
    assert isinstance(candidate_gen, dict)
    del candidate_gen["top_p"]
    candidate["gen_params"] = candidate_gen
    metric_a, metric_b = _metric(), _metric()
    metric_b["threshold"] = None
    result = compare_declarations(baseline, candidate, metric_a, metric_b)
    assert result["declared_relation"] == "different"
    assert result["differences"] == [
        {"field": "conditions.model", "baseline": "model-a", "candidate": "model-b"}
    ]
    assert {tuple(item.values()) for item in result["unknowns"]} == {
        ("candidate", "conditions.gen_params.top_p", "missing"),
        ("candidate", "metric.threshold", "unknown"),
    }


def test_null_seed_and_context_are_unknown_even_when_controlled_accepts_null() -> None:
    baseline, candidate = _conditions(), _conditions()
    baseline_gen, candidate_gen = baseline["gen_params"], candidate["gen_params"]
    assert isinstance(baseline_gen, dict) and isinstance(candidate_gen, dict)
    baseline_gen["seed"] = None
    candidate_gen["num_ctx"] = None
    result = compare_declarations(baseline, candidate, _metric(), _metric())
    assert result["declared_relation"] == "unknown"
    assert result["differences"] == []
    assert ("baseline", "conditions.gen_params.seed", "unknown") in {
        tuple(item.values()) for item in result["unknowns"]
    }
    assert ("candidate", "conditions.gen_params.num_ctx", "unknown") in {
        tuple(item.values()) for item in result["unknowns"]
    }


def test_missing_metric_field_and_zero_threshold_have_distinct_meanings() -> None:
    metric_a, metric_b = _metric(), _metric()
    del metric_b["threshold"]
    result = compare_declarations(_conditions(), _conditions(), metric_a, metric_b)
    assert result["declared_relation"] == "unknown"
    assert result["unknowns"] == [
        {"side": "candidate", "field": "metric.threshold", "reason": "missing"}
    ]
    assert compare_declarations(_conditions(), _conditions(), _metric(), _metric())[
        "declared_relation"
    ] == "same"


def test_suite_and_metric_direction_differences_are_retained() -> None:
    candidate = _conditions()
    candidate["suite"] = "suite-b"
    metric = _metric()
    metric["higher_is_better"] = False
    result = compare_declarations(_conditions(), candidate, _metric(), metric)
    assert result["declared_relation"] == "different"
    assert [item["field"] for item in result["differences"]] == [
        "conditions.suite",
        "metric.higher_is_better",
    ]


@pytest.mark.parametrize("field,value", [("higher_is_better", 1), ("threshold", True)])
def test_boolean_and_numeric_metric_fields_never_coerce(field: str, value: object) -> None:
    metric = _metric()
    metric[field] = value
    with pytest.raises(DeclarationError, match="INVALID_METRIC_FIELD"):
        compare_declarations(_conditions(), _conditions(), metric, _metric())


def test_boolean_generation_number_becomes_unknown_instead_of_equal_to_one() -> None:
    candidate = _conditions()
    generation = candidate["gen_params"]
    assert isinstance(generation, dict)
    generation["top_p"] = True
    result = compare_declarations(_conditions(), candidate, _metric(), _metric())
    assert result["declared_relation"] == "unknown"
    assert {tuple(item.values()) for item in result["unknowns"]} == {
        ("candidate", "conditions.gen_params.top_p", "unknown")
    }
