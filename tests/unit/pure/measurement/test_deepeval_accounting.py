"""Presence, unit and resource contracts for descriptive metric accounting."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest
from underwrite_core.canonical import SAFE_INT_MAX

from underwrite.instrument.evidence.deepeval_accounting import (
    LIMITATIONS,
    LIMITATIONS_V2,
    MAX_CASES,
    MAX_ERROR_LENGTH,
    MAX_METRICS,
    MAX_NAME_LENGTH,
    RULE_PROFILE,
    RULE_PROFILE_V2,
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
    data[key] = [{}] if key == "conversationalTestCases" else {"agent": {"span": {}}}
    with pytest.raises(DeepevalAccountingError) as caught:
        run(data)
    assert caught.value.code == "UNSUPPORTED_PROFILE"
    with pytest.raises(DeepevalAccountingError) as caught:
        run(source(**{key: None}))
    assert caught.value.code == "INVALID_INPUT"


def test_trace_scope_is_explicit_without_changing_metric_counting() -> None:
    data = source()
    data["testCases"][0]["trace"] = {"spans": []}
    result = run(data)
    assert result["scope"]["trace"] == "present_unanalyzed"
    assert result["counts"] == run(source())["counts"]
    assert result["next_actions"][-1] == "INSPECT_TRACE_INDEX"


@pytest.mark.parametrize("rule", [RULE_PROFILE, RULE_PROFILE_V2])
def test_mixed_trace_population_marks_whole_run_and_leaves_old_shapes_identical(rule: str) -> None:
    data = v2_source() if rule == RULE_PROFILE_V2 else source()
    baseline = account_deepeval(data, rule_profile=rule)
    empty_values: tuple[object, ...] = (None, {})
    for empty in empty_values:
        candidate = deepcopy(data)
        candidate["testCases"][0]["trace"] = empty
        assert account_deepeval(candidate, rule_profile=rule) == baseline
    traced = deepcopy(data)
    untraced: dict[str, object] = {"name": "untraced"}
    if rule == RULE_PROFILE_V2:
        untraced["flaky"] = False
    traced["testCases"].append(untraced)
    traced["testCases"][0]["trace"] = {"opaque": "unverified"}
    result = cast(dict[str, Any], account_deepeval(traced, rule_profile=rule))
    assert result["scope"]["trace"] == "present_unanalyzed"
    assert result["counts"]["metrics"]["total"] == 1
    assert result["next_actions"][-1] == "INSPECT_TRACE_INDEX"


@pytest.mark.parametrize("trace", [False, [], "private", 1])
def test_non_object_trace_refuses_at_source_pointer(trace: object) -> None:
    data = source()
    data["testCases"][0]["trace"] = trace
    with pytest.raises(DeepevalAccountingError) as caught:
        run(data)
    assert (caught.value.code, caught.value.location) == ("INVALID_INPUT", "/testCases/0/trace")


def test_source_success_null_still_refuses_with_empty_trace() -> None:
    data = source()
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


@pytest.mark.parametrize(
    "groups", [{}, {"agent": {}}, {"agent": {}, "tool": {}, "retriever": {}, "llm": {}, "base": {}}]
)
def test_empty_trace_metric_groups_are_ordinary_case_scope(groups: object) -> None:
    assert run(source(traceMetricsScores=groups)) == run(source())


def test_trace_metric_list_is_wrong_source_container() -> None:
    with pytest.raises(DeepevalAccountingError) as caught:
        run(source(traceMetricsScores=[]))
    assert caught.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("value", [None, [], "", False])
def test_trace_metric_root_wrong_types_have_bounded_errors(value: object) -> None:
    with pytest.raises(DeepevalAccountingError) as caught:
        run(source(traceMetricsScores=value))
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.reason == "DEEPEVAL_OBJECT_REQUIRED"
    assert caught.value.location == "/traceMetricsScores"


@pytest.mark.parametrize("value", [None, [], "", False])
def test_trace_metric_group_wrong_types_have_known_group_path(value: object) -> None:
    with pytest.raises(DeepevalAccountingError) as caught:
        run(source(traceMetricsScores={"agent": value}))
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.location == "/traceMetricsScores/agent"


@pytest.mark.parametrize(
    "groups", [{"private-source-text": {}}, {1: {}}, {object(): None for _ in range(6)}]
)
def test_unknown_or_oversized_trace_groups_do_not_echo_keys(groups: object) -> None:
    with pytest.raises(DeepevalAccountingError) as caught:
        run(source(traceMetricsScores=groups))
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.reason == "DEEPEVAL_TRACE_METRIC_GROUP_INVALID"
    assert caught.value.location == "/traceMetricsScores"
    assert "private-source-text" not in str(caught.value)


@pytest.mark.parametrize("group", ["agent", "tool", "retriever", "llm", "base"])
def test_each_known_trace_group_only_admits_an_empty_mapping(group: str) -> None:
    assert run(source(traceMetricsScores={group: {}})) == run(source())
    with pytest.raises(DeepevalAccountingError) as caught:
        run(source(traceMetricsScores={group: {"declared_span": {}}}))
    assert caught.value.code == "UNSUPPORTED_PROFILE"
    assert caught.value.reason == "DEEPEVAL_SCOPE_UNSUPPORTED"
    assert caught.value.location == "/traceMetricsScores/" + group


def test_default_v1_canonical_output_is_unchanged() -> None:
    from underwrite_core.canonical import content_digest

    payload = {
        "testCases": [
            {
                "name": "case",
                "success": False,
                "metricsData": [
                    {"name": "zero", "threshold": 0.5, "success": False, "score": 0},
                    {
                        "name": "error",
                        "threshold": 0.5,
                        "success": False,
                        "error": "evaluation failed",
                    },
                    {"name": "pending", "threshold": 0.5, "success": True},
                ],
            }
        ],
        "testPassed": 0,
        "testFailed": 1,
    }
    assert content_digest(account_deepeval(payload)) == (
        "sha256:e162c3defd6d05bff7815b23fc5616596cf23c56e8773e608101599c5a2bf5b7"
    )


def v2_source(metrics: list[Any] | None = None) -> dict[str, Any]:
    payload = source([metric(flaky=False)] if metrics is None else metrics)
    payload["testCases"][0]["flaky"] = False
    return payload


def v2_run(payload: object) -> dict[str, Any]:
    return account_deepeval(payload, rule_profile=RULE_PROFILE_V2)


@pytest.mark.parametrize("omit_success", [False, True])
@pytest.mark.parametrize("omit_threshold", [False, True])
@pytest.mark.parametrize("state", ["scored", "error", "unscored"])
def test_v2_absent_verdict_threshold_and_metric_state_are_independent(
    omit_success: bool,
    omit_threshold: bool,
    state: str,
) -> None:
    row = metric(flaky=True, score=0, success=False)
    if state != "scored":
        del row["score"]
    if state == "error":
        row["error"] = "evaluation failed"
    if omit_success:
        del row["success"]
    if omit_threshold:
        del row["threshold"]
    result = v2_run(v2_source([row]))
    observed = result["cases"][0]["metrics"][0]
    assert observed["state"] == state
    assert observed["source_success"] is (None if omit_success else False)
    assert observed["threshold"] == (None if omit_threshold else 0.5)
    assert observed["source_flaky"] is True
    counts = result["counts"]["metrics"]
    assert counts["scored_source_success_absent"] == int(state == "scored" and omit_success)
    assert counts["scored_source_false"] == int(state == "scored" and not omit_success)
    assert counts["observed_zero"] == int(state == "scored")
    assert counts["source_flaky_true"] == 1


@pytest.mark.parametrize("level", ["case", "metric"])
@pytest.mark.parametrize("value", ["missing", None, 0, 1, "false"])
def test_v2_flaky_is_required_exact_boolean(level: str, value: object) -> None:
    payload = v2_source()
    case = payload["testCases"][0]
    row = case if level == "case" else case["metricsData"][0]
    if value == "missing":
        del row["flaky"]
    else:
        row["flaky"] = value
    with pytest.raises(DeepevalAccountingError, match="DEEPEVAL_BOOLEAN_REQUIRED"):
        v2_run(payload)


@pytest.mark.parametrize(
    "key,value",
    [
        ("success", None),
        ("success", 0),
        ("threshold", None),
        ("threshold", True),
        ("threshold", float("nan")),
        ("threshold", SAFE_INT_MAX + 1),
    ],
)
def test_v2_present_optional_fields_are_not_absence(key: str, value: object) -> None:
    with pytest.raises(DeepevalAccountingError):
        v2_run(v2_source([metric(flaky=False, **{key: value})]))


def test_v2_flaky_and_missing_verdict_counts_preserve_partitions_and_source_case_flags() -> None:
    payload = v2_source(
        [
            metric(flaky=True, score=0, success=False),
            metric(flaky=False),
            {"name": "same", "score": 0.3, "flaky": False},
            {"name": "same", "error": "failure", "flaky": False},
            {"name": "same", "flaky": True},
        ]
    )
    payload["testCases"].append({"name": "case", "success": False, "flaky": True})
    payload.update(testPassed=1, testFailed=1, metricsScores=[{"errors": 999}])
    result = v2_run(payload)
    assert result["counts"] == {
        "cases": {
            "total": 2,
            "source_true": 1,
            "source_false": 1,
            "source_success_absent": 0,
            "source_flaky_true": 1,
        },
        "metrics": {
            "total": 5,
            "scored": 3,
            "error": 1,
            "unscored": 1,
            "observed_zero": 1,
            "scored_source_true": 1,
            "scored_source_false": 1,
            "scored_source_success_absent": 1,
            "source_flaky_true": 2,
        },
    }
    assert result["declared_case_counts"]["comparison"] == {
        "passed": "matches",
        "failed": "matches",
    }
    assert result["cases"][0]["source_success"] is True
    assert result["cases"][1]["source_success"] is False
    assert [row["metric_index"] for row in result["cases"][0]["metrics"]] == list(range(5))
    assert result["limitations"] == list(LIMITATIONS_V2)
    assert LIMITATIONS_V2 == (*LIMITATIONS, "FLAKY_DECLARATIONS_NOT_CI_OUTCOMES")
    assert result["scope"]["metric_aggregates"] == "not_checked"


def test_v2_empty_population_and_absent_case_flag_remain_unknown() -> None:
    assert v2_run({"testCases": []})["counts"]["metrics"]["total"] == 0
    result = v2_run({"testCases": [{"name": "case", "flaky": False}], "testPassed": 0})
    assert result["counts"]["cases"]["source_success_absent"] == 1
    assert result["declared_case_counts"]["comparison"]["passed"] == "not_comparable"


def test_pure_rule_profile_selection_is_explicit_and_unknown_is_sanitized() -> None:
    payload = source()
    assert account_deepeval(payload) == account_deepeval(payload, rule_profile=RULE_PROFILE)
    with pytest.raises(DeepevalAccountingError) as caught:
        account_deepeval(None, rule_profile="secret")
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_INPUT",
        "DEEPEVAL_RULE_PROFILE_UNSUPPORTED",
        "/rule_profile",
    )
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "fault", ["score_error", "case_null", "trace_non_object", "conversation", "population"]
)
def test_v2_retains_shared_refusals(fault: str) -> None:
    payload = v2_source()
    expected = "INVALID_INPUT"
    if fault == "score_error":
        payload["testCases"][0]["metricsData"][0]["error"] = "secret"
    elif fault == "case_null":
        payload["testCases"][0]["success"] = None
    elif fault == "trace_non_object":
        payload["testCases"][0]["trace"] = []
    elif fault == "conversation":
        payload["conversationalTestCases"] = [{}]
        expected = "UNSUPPORTED_PROFILE"
    else:
        payload["testCases"][0]["metricsData"] = [object()] * (MAX_METRICS + 1)
        expected = "INPUT_LIMIT_EXCEEDED"
    with pytest.raises(DeepevalAccountingError) as caught:
        v2_run(payload)
    assert caught.value.code == expected
    assert "secret" not in str(caught.value)
