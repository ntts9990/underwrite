"""Describe DeepEval metric occurrences without inferring task success or missing runs."""

from __future__ import annotations

import unicodedata
from types import MappingProxyType
from typing import cast

from underwrite_core.canonical import SAFE_INT_MAX, CanonicalizationError, canonical_bytes

MAX_CASES = 512
MAX_METRICS = 2048
MAX_NAME_LENGTH = 256
MAX_ERROR_LENGTH = 1024
TRACE_METRIC_GROUPS = frozenset({"agent", "tool", "retriever", "llm", "base"})
RULE_PROFILE = "deepeval.metric-accounting.v1"
RULE_PROFILE_V2 = "deepeval.metric-accounting.v2"
RULES = MappingProxyType(
    {
        "scored": "SCORE_PRESENT",
        "error": "ERROR_PRESENT",
        "unscored": "SCORE_AND_ERROR_ABSENT",
    }
)
LIMITATIONS = (
    "SOURCE_CLAIMS_UNVERIFIED",
    "PLANNED_INVENTORY_UNKNOWN",
    "METRIC_AGGREGATES_NOT_CHECKED",
    "NO_CAUSAL_FAILURE_ATTRIBUTION",
    "NO_STATISTICAL_OR_APPROVAL_AUTHORITY",
)
LIMITATIONS_V2 = (*LIMITATIONS, "FLAKY_DECLARATIONS_NOT_CI_OUTCOMES")
NEXT_ACTIONS = (
    "INSPECT_EVALUATOR_ERRORS",
    "SUPPLY_PLANNED_INVENTORY_FOR_MISSINGNESS",
    "CHECK_PRODUCER_CASE_COUNTS",
    "INSPECT_TRACE_INDEX",
)
SCOPE = MappingProxyType(
    {
        "unit": "metric_occurrence_within_test_case",
        "test_cases": "included",
        "conversational": "absent_or_empty",
        "trace": "absent_or_empty",
        "metric_aggregates": "not_checked",
        "planned_inventory": "unknown",
    }
)


class DeepevalAccountingError(ValueError):
    """Structural, code-owned refusal without source text in the diagnostic."""

    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _error(reason: str, location: str, code: str = "INVALID_INPUT") -> DeepevalAccountingError:
    return DeepevalAccountingError(code, reason, location)


def _object(value: object, location: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _error("DEEPEVAL_OBJECT_REQUIRED", location)
    return cast(dict[str, object], value)


def _array(value: object, maximum: int, location: str) -> list[object]:
    if type(value) is not list:
        raise _error("DEEPEVAL_ARRAY_REQUIRED", location)
    items = cast(list[object], value)
    if len(items) > maximum:
        raise _error("DEEPEVAL_POPULATION_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    return items


def _text(value: object, maximum: int, location: str, *, blank: bool = False) -> str:
    if type(value) is not str:
        raise _error("DEEPEVAL_TEXT_REQUIRED", location)
    if len(value) > maximum:
        raise _error("DEEPEVAL_TEXT_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    if not blank and not value.strip():
        raise _error("DEEPEVAL_TEXT_REQUIRED", location)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _error("DEEPEVAL_TEXT_INVALID", location) from None
    if not unicodedata.is_normalized("NFC", value):
        raise _error("DEEPEVAL_TEXT_INVALID", location)
    return value


def _number(value: object, location: str) -> int | float:
    if type(value) is not int and type(value) is not float:
        raise _error("DEEPEVAL_NUMBER_INVALID", location)
    try:
        canonical_bytes(value)
    except CanonicalizationError:
        raise _error("DEEPEVAL_NUMBER_INVALID", location) from None
    return value


def _integer(value: object, location: str) -> int:
    if type(value) is not int or not 0 <= value <= SAFE_INT_MAX:
        raise _error("DEEPEVAL_NONNEGATIVE_INTEGER_REQUIRED", location)
    return value


def _flag(value: object, location: str) -> bool:
    if type(value) is not bool:
        raise _error("DEEPEVAL_BOOLEAN_REQUIRED", location)
    return value


def _presence(obj: dict[str, object], key: str, location: str) -> dict[str, object]:
    if key not in obj:
        return {"present": False}
    return {"present": True, "value": _integer(obj[key], location)}


def _trace_metrics_scope(value: object) -> None:
    location = "/traceMetricsScores"
    groups = _object(value, location)
    if len(groups) > len(TRACE_METRIC_GROUPS):
        raise _error("DEEPEVAL_TRACE_METRIC_GROUP_INVALID", location)
    if any(type(key) is not str or key not in TRACE_METRIC_GROUPS for key in groups):
        raise _error("DEEPEVAL_TRACE_METRIC_GROUP_INVALID", location)
    for key, group in groups.items():
        entries = _object(group, location + "/" + key)
        if entries:
            raise _error("DEEPEVAL_SCOPE_UNSUPPORTED", location + "/" + key, "UNSUPPORTED_PROFILE")


def _scope(payload: dict[str, object]) -> None:
    key = "conversationalTestCases"
    if key in payload:
        items = _array(payload[key], MAX_METRICS, "/" + key)
        if items:
            raise _error("DEEPEVAL_SCOPE_UNSUPPORTED", "/" + key, "UNSUPPORTED_PROFILE")
    if "traceMetricsScores" in payload:
        _trace_metrics_scope(payload["traceMetricsScores"])


def _case_trace(case: dict[str, object], location: str) -> bool:
    value = case.get("trace")
    if value is None:
        return False
    return bool(_object(value, location + "/trace"))


def _metric(value: object, index: int, location: str, rule_profile: str) -> dict[str, object]:
    metric = _object(value, location)
    scored, errored = "score" in metric, "error" in metric
    if scored and errored:
        raise _error("DEEPEVAL_METRIC_SCORE_AND_ERROR", location)
    state = "scored" if scored else "error" if errored else "unscored"
    result: dict[str, object] = {
        "metric_index": index,
        "name": _text(metric.get("name"), MAX_NAME_LENGTH, location + "/name"),
        "state": state,
        "applied_rule": RULES[state],
        "source_success": (
            _flag(metric.get("success"), location + "/success")
            if rule_profile == RULE_PROFILE or "success" in metric
            else None
        ),
        "threshold": (
            _number(metric.get("threshold"), location + "/threshold")
            if rule_profile == RULE_PROFILE or "threshold" in metric
            else None
        ),
    }
    if rule_profile == RULE_PROFILE_V2:
        result["source_flaky"] = _flag(metric.get("flaky"), location + "/flaky")
    if scored:
        result["score"] = _number(metric["score"], location + "/score")
    elif errored:
        result["error"] = _text(metric["error"], MAX_ERROR_LENGTH, location + "/error", blank=True)
    return result


def _declared_counts(payload: dict[str, object], cases: dict[str, int]) -> dict[str, object]:
    passed = _presence(payload, "testPassed", "/testPassed")
    failed = _presence(payload, "testFailed", "/testFailed")
    comparison: dict[str, str] = {}
    for key, declaration, expected in (
        ("passed", passed, cases["source_true"]),
        ("failed", failed, cases["source_false"]),
    ):
        if not declaration["present"]:
            comparison[key] = "not_declared"
        elif cases["source_success_absent"]:
            comparison[key] = "not_comparable"
        else:
            comparison[key] = "matches" if declaration["value"] == expected else "differs"
    return {"passed": passed, "failed": failed, "comparison": comparison}


def account_deepeval(payload: object, *, rule_profile: str = RULE_PROFILE) -> dict[str, object]:
    """Apply presence rules to ordinary test cases in a normalized observation payload."""
    if rule_profile not in (RULE_PROFILE, RULE_PROFILE_V2):
        raise _error("DEEPEVAL_RULE_PROFILE_UNSUPPORTED", "/rule_profile")
    source = _object(payload, "/")
    rows = _array(source.get("testCases"), MAX_CASES, "/testCases")
    _scope(source)
    cases: list[dict[str, object]] = []
    case_counts = {
        "total": len(rows),
        "source_true": 0,
        "source_false": 0,
        "source_success_absent": 0,
    }
    metric_counts = {
        "total": 0,
        "scored": 0,
        "error": 0,
        "unscored": 0,
        "observed_zero": 0,
        "scored_source_true": 0,
        "scored_source_false": 0,
    }
    if rule_profile == RULE_PROFILE_V2:
        case_counts["source_flaky_true"] = 0
        metric_counts["source_flaky_true"] = 0
        metric_counts["scored_source_success_absent"] = 0
    trace_present = False
    for index, raw in enumerate(rows):
        location = f"/testCases/{index}"
        case = _object(raw, location)
        metrics = _array(
            case.get("metricsData", []),
            MAX_METRICS - metric_counts["total"],
            location + "/metricsData",
        )
        trace_present = _case_trace(case, location) or trace_present
        flag = _flag(case["success"], location + "/success") if "success" in case else None
        case_counts[
            "source_success_absent" if flag is None else "source_true" if flag else "source_false"
        ] += 1
        normalized = [
            _metric(item, n, f"{location}/metricsData/{n}", rule_profile)
            for n, item in enumerate(metrics)
        ]
        for metric in normalized:
            metric_counts["total"] += 1
            metric_counts[cast(str, metric["state"])] += 1
            if rule_profile == RULE_PROFILE_V2:
                metric_counts["source_flaky_true"] += int(cast(bool, metric["source_flaky"]))
            if metric["state"] == "scored":
                metric_counts["observed_zero"] += int(metric["score"] == 0)
                metric_counts[
                    "scored_source_success_absent"
                    if metric["source_success"] is None
                    else "scored_source_true"
                    if metric["source_success"]
                    else "scored_source_false"
                ] += 1
        result: dict[str, object] = {
            "case_index": index,
            "name": _text(case.get("name"), MAX_NAME_LENGTH, location + "/name"),
            "order": _presence(case, "order", location + "/order"),
            "source_success": flag,
            "metrics": normalized,
        }
        if rule_profile == RULE_PROFILE_V2:
            result["source_flaky"] = _flag(case.get("flaky"), location + "/flaky")
            case_counts["source_flaky_true"] += int(result["source_flaky"])
        cases.append(result)
    declared = _declared_counts(source, case_counts)
    actions = ["SUPPLY_PLANNED_INVENTORY_FOR_MISSINGNESS"]
    if metric_counts["error"]:
        actions.insert(0, "INSPECT_EVALUATOR_ERRORS")
    if "differs" in cast(dict[str, str], declared["comparison"]).values():
        actions.append("CHECK_PRODUCER_CASE_COUNTS")
    actions.extend(["INSPECT_TRACE_INDEX"] if trace_present else [])
    return {
        "rule_profile": rule_profile,
        "scope": {**SCOPE, "trace": "present_unanalyzed"} if trace_present else dict(SCOPE),
        "cases": cases,
        "counts": {"cases": case_counts, "metrics": metric_counts},
        "declared_case_counts": declared,
        "limitations": list(LIMITATIONS if rule_profile == RULE_PROFILE else LIMITATIONS_V2),
        "next_actions": actions,
    }
