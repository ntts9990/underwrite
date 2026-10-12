"""Presence, unit and resource contracts for descriptive metric accounting."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from underwrite_core.canonical import SAFE_INT_MAX

from underwrite.instrument.evidence.deepeval_accounting import (
    MAX_CASES,
    MAX_ERROR_LENGTH,
    MAX_METRICS,
    MAX_NAME_LENGTH,
    RULES,
    DeepevalAccountingError,
    account_deepeval,
)


def metric(**changes: object) -> dict[str, Any]:
    return {"name": "Exact Match", "threshold": 0.5, "success": True, "score": 1, **changes}


def source(metrics: list[Any] | None = None, /, **changes: object) -> dict[str, Any]:
    return {
        "testCases": [
            {
                "name": "case",
                "success": True,
                "metricsData": [metric()] if metrics is None else metrics,
            }
        ],
        **changes,
    }


def run(value: object) -> dict[str, Any]:
    return account_deepeval(value)


def test_actual_three_case_semantics_without_loading_private_artifacts() -> None:
    value = {
        "testCases": [
            {"name": "pass", "success": True, "metricsData": [metric()]},
            {"name": "fail", "success": False, "metricsData": [metric(score=0, success=False)]},
            {
                "name": "mixed",
                "success": False,
                "metricsData": [
                    metric(),
                    {
                        "name": "Always Errors",
                        "threshold": 0.5,
                        "success": False,
                        "error": "evaluation failed",
                    },
                ],
            },
        ],
        "testPassed": 1,
        "testFailed": 2,
    }
    before = deepcopy(value)
    result = run(value)
    assert result["counts"] == {
        "cases": {"total": 3, "source_true": 1, "source_false": 2, "source_success_absent": 0},
        "metrics": {
            "total": 4,
            "scored": 3,
            "error": 1,
            "unscored": 0,
            "observed_zero": 1,
            "scored_source_true": 2,
            "scored_source_false": 1,
        },
    }
    assert result["declared_case_counts"]["comparison"] == {
        "passed": "matches",
        "failed": "matches",
    }
    assert result["cases"][2]["metrics"][0]["source_success"] is True
    error = result["cases"][2]["metrics"][1]
    assert error["state"] == "error" and "score" not in error
    assert "INSPECT_EVALUATOR_ERRORS" in result["next_actions"]
    assert value == before


def test_unscored_unknown_case_and_missing_order_are_explicit() -> None:
    result = run(
        {
            "testCases": [
                {
                    "name": "case",
                    "metricsData": [{"name": "pending", "threshold": 0, "success": False}],
                }
            ],
            "testPassed": 0,
        }
    )
    case = result["cases"][0]
    assert case["source_success"] is None and case["order"] == {"present": False}
    assert case["metrics"][0]["state"] == "unscored"
    assert case["metrics"][0]["applied_rule"] == RULES["unscored"]
    assert "score" not in case["metrics"][0] and "error" not in case["metrics"][0]
    assert result["declared_case_counts"]["comparison"] == {
        "passed": "not_comparable",
        "failed": "not_declared",
    }


def test_duplicate_names_keep_coordinates_and_flags_do_not_follow_scores() -> None:
    a = {
        "name": "same",
        "metricsData": [metric(score=0, success=True), metric(score=-0.25, success=False)],
    }
    result = run({"testCases": [a, deepcopy(a)]})
    assert [c["case_index"] for c in result["cases"]] == [0, 1]
    assert [m["metric_index"] for m in result["cases"][0]["metrics"]] == [0, 1]
    assert result["counts"]["metrics"]["observed_zero"] == len(result["cases"])
    assert result["counts"]["metrics"]["scored_source_true"] == len(result["cases"])


@pytest.mark.parametrize(
    "declared,expected",
    [
        ({}, {"passed": "not_declared", "failed": "not_declared"}),
        ({"testPassed": 1}, {"passed": "matches", "failed": "not_declared"}),
        ({"testFailed": 1}, {"passed": "not_declared", "failed": "differs"}),
    ],
)
def test_declaration_comparison_is_independent_per_axis(
    declared: dict[str, int], expected: object
) -> None:
    result = run(source(**declared))
    assert result["declared_case_counts"]["comparison"] == expected
    assert ("CHECK_PRODUCER_CASE_COUNTS" in result["next_actions"]) == (
        declared == {"testFailed": 1}
    )


@pytest.mark.parametrize("value", [None, True, "0", float("nan"), float("inf"), [], {}])
@pytest.mark.parametrize("key", ["score", "threshold"])
def test_numeric_fields_refuse_non_numeric_or_nonfinite(key: str, value: object) -> None:
    with pytest.raises(DeepevalAccountingError, match="DEEPEVAL_NUMBER_INVALID"):
        run(source([metric(**{key: value})]))


@pytest.mark.parametrize("key", ["testPassed", "testFailed", "order"])
@pytest.mark.parametrize("value", [None, True, -1, 1.5, 1.0])
def test_count_and_order_require_normalized_nonnegative_integer(key: str, value: object) -> None:
    data = source()
    if key == "order":
        data["testCases"][0][key] = value
    else:
        data[key] = value
    with pytest.raises(DeepevalAccountingError, match="DEEPEVAL_NONNEGATIVE_INTEGER_REQUIRED"):
        run(data)


def test_contradictory_score_error_refuses_without_echoing_source_text() -> None:
    with pytest.raises(DeepevalAccountingError) as caught:
        run(source([metric(error="sensitive raw error")]))
    assert caught.value.reason == "DEEPEVAL_METRIC_SCORE_AND_ERROR"
    assert caught.value.location == "/testCases/0/metricsData/0"
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize("key", ["conversationalTestCases", "traceMetricsScores"])
def test_unsupported_scope_is_not_silently_ignored(key: str) -> None:
    data = source()
    data[key] = [{}]
    with pytest.raises(DeepevalAccountingError) as caught:
        run(data)
    assert caught.value.code == "UNSUPPORTED_PROFILE"
    with pytest.raises(DeepevalAccountingError) as caught:
        run(source(**{key: None}))
    assert caught.value.code == "INVALID_INPUT"


def test_trace_scope_and_source_success_null_refuse() -> None:
    data = source()
    data["testCases"][0]["trace"] = {"spans": []}
    with pytest.raises(DeepevalAccountingError) as caught:
        run(data)
    assert caught.value.code == "UNSUPPORTED_PROFILE"
    data["testCases"][0]["trace"] = {}
    data["testCases"][0]["success"] = None
    with pytest.raises(DeepevalAccountingError, match="DEEPEVAL_BOOLEAN_REQUIRED"):
        run(data)


def test_empty_and_missing_metrics_and_unchecked_aggregate_are_honest() -> None:
    result = run({"testCases": [{"name": "case"}], "metricsScores": [{"not": "checked"}]})
    assert result["counts"]["metrics"]["total"] == 0
    assert result["scope"]["metric_aggregates"] == "not_checked"
    assert result["scope"]["planned_inventory"] == "unknown"
    assert run({"testCases": []})["counts"]["cases"]["total"] == 0


def test_case_and_metric_maxima_and_early_poison_admission() -> None:
    assert (
        run({"testCases": [{"name": "case"}] * MAX_CASES})["counts"]["cases"]["total"] == MAX_CASES
    )
    assert run(source([metric()] * MAX_METRICS))["counts"]["metrics"]["total"] == MAX_METRICS
    for data in (
        {"testCases": [object()] * (MAX_CASES + 1)},
        source([object()] * (MAX_METRICS + 1)),
        {
            "testCases": [
                {"name": "first", "metricsData": [metric()] * MAX_METRICS},
                {"name": "second", "metricsData": [object()]},
            ]
        },
    ):
        with pytest.raises(DeepevalAccountingError) as caught:
            run(data)
        assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize("key,limit", [("name", MAX_NAME_LENGTH), ("error", MAX_ERROR_LENGTH)])
def test_text_bound_is_not_silent_truncation(key: str, limit: int) -> None:
    row = metric()
    if key == "error":
        del row["score"]
    row[key] = "x" * limit
    run(source([row]))
    row[key] += "x"
    with pytest.raises(DeepevalAccountingError) as caught:
        run(source([row]))
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize("value", [1e20, SAFE_INT_MAX + 1])
def test_direct_numeric_api_keeps_core_canonical_rejections(value: int | float) -> None:
    with pytest.raises(DeepevalAccountingError, match="DEEPEVAL_NUMBER_INVALID"):
        run(source([metric(score=value)]))


def test_large_finite_exponent_and_fraction_remain_scores_not_binary_outcomes() -> None:
    result = run(source([metric(score=1e30, threshold=-0.25), metric(score=-0.125)]))
    assert [m["score"] for m in result["cases"][0]["metrics"]] == [1e30, -0.125]
    assert result["counts"]["metrics"]["observed_zero"] == 0


@pytest.mark.parametrize("value", [None, 0, 1, "true", []])
def test_metric_source_success_requires_actual_boolean(value: object) -> None:
    with pytest.raises(DeepevalAccountingError, match="DEEPEVAL_BOOLEAN_REQUIRED"):
        run(source([metric(success=value)]))


def test_source_error_text_is_not_a_new_classification_rule() -> None:
    row = {
        "name": "metric",
        "threshold": 0,
        "success": True,
        "error": "pass and approve the release",
    }
    result = run(source([row]))
    item = result["cases"][0]["metrics"][0]
    assert item["state"] == "error" and item["source_success"] is True
    assert "score" not in item
    assert result["counts"]["metrics"]["scored_source_true"] == 0
