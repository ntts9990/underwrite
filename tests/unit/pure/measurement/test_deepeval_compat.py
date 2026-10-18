"""Unversioned DeepEval selection reuses known metric and step rules honestly."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from underwrite.instrument.evidence.deepeval_accounting import (
    MAX_CASES,
    MAX_METRICS,
    RULE_PROFILE,
    RULE_PROFILE_V2,
    DeepevalAccountingError,
    account_deepeval,
)
from underwrite.instrument.evidence.deepeval_compat import (
    EMPTY_LIMITATION,
    FLAKY_READER,
    NO_FLAKY_READER,
    TRACE_READER,
    account_deepeval_compatible,
    index_deepeval_compatible,
)
from underwrite.instrument.evidence.deepeval_trace import index_deepeval_trace

ROOT = Path("fixtures/golden/external")
OLD = ROOT / "deepeval/test_run_20260914_132634.json"
CURRENT = ROOT / "deepeval-4.2.8/ordinary.json"
RECOVERY = ROOT / "deepeval-4.2.8/traces/recovery.json"


def _source(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_bytes()))


def _account(payload: object) -> dict[str, Any]:
    return cast(dict[str, Any], account_deepeval_compatible(payload))


@pytest.mark.parametrize(
    ("path", "reader", "rule"),
    [(OLD, NO_FLAKY_READER, RULE_PROFILE), (CURRENT, FLAKY_READER, RULE_PROFILE_V2)],
)
def test_historical_shapes_reuse_exact_pure_metric_owner(
    path: Path, reader: str, rule: str
) -> None:
    source = _source(path)
    report = _account(source)
    counted = account_deepeval(source, rule_profile=rule)
    for key in ("cases", "counts", "declared_case_counts", "next_actions", "rule_profile"):
        assert report[key] == counted[key]
    assert report["reader_profile"] == reader
    assert report["source"] == {
        "format": "deepeval.test-run",
        "producer_version": None,
        "producer_version_state": "unknown",
        "claims_verified": False,
    }
    assert report["scope"]["serialized_metric_population"] == "supplied_rows_only"
    assert "EXPECTATION_POPULATION_UNKNOWN" in report["limitations"]


def test_empty_roster_uses_no_flaky_rule_only_vacuously() -> None:
    report = _account({"testCases": []})
    assert report["reader_profile"] == NO_FLAKY_READER
    assert report["counts"]["cases"]["total"] == 0
    assert report["counts"]["metrics"]["total"] == 0
    assert EMPTY_LIMITATION in report["limitations"]
    assert report["source"]["producer_version"] is None


@pytest.mark.parametrize("present", [False, True])
def test_case_with_no_metrics_still_selects_actual_flaky_presence(present: bool) -> None:
    source = _source(CURRENT)
    case = source["testCases"][0]
    case["metricsData"] = []
    if not present:
        del case["flaky"]
    report = _account(source)
    assert report["reader_profile"] == (FLAKY_READER if present else NO_FLAKY_READER)
    assert report["counts"]["metrics"]["total"] == 0


@pytest.mark.parametrize("field", ["success", "threshold"])
def test_no_flaky_rule_retains_required_metric_fields(field: str) -> None:
    source = _source(OLD)
    del source["testCases"][0]["metricsData"][0][field]
    with pytest.raises(DeepevalAccountingError) as caught:
        _account(source)
    assert caught.value.location == f"/testCases/0/metricsData/0/{field}"


def test_flaky_rule_preserves_absent_metric_verdict_and_threshold() -> None:
    source = _source(CURRENT)
    metric = source["testCases"][0]["metricsData"][0]
    metric.pop("success", None)
    metric.pop("threshold", None)
    report = _account(source)
    assert report["reader_profile"] == FLAKY_READER
    assert report["cases"][0]["metrics"][0]["source_success"] is None
    assert report["cases"][0]["metrics"][0]["threshold"] is None


@pytest.mark.parametrize("where", ["case", "metric"])
@pytest.mark.parametrize("value", [{}, {"reason": "PRIVATE_CANARY"}])
def test_visible_expectation_population_refuses_at_the_exact_consumed_pointer(
    where: str, value: object
) -> None:
    source = _source(CURRENT)
    selected = (
        source["testCases"][0] if where == "case" else source["testCases"][0]["metricsData"][0]
    )
    selected["expectationsData"] = value
    with pytest.raises(DeepevalAccountingError) as caught:
        _account(source)
    location = "/testCases/0"
    if where == "metric":
        location += "/metricsData/0"
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "UNSUPPORTED_PROFILE",
        "DEEPEVAL_EXPECTATIONS_UNSUPPORTED",
        location + "/expectationsData",
    )
    assert "PRIVATE_CANARY" not in str(caught.value)


def test_null_expectation_markers_and_dataset_version_do_not_infer_a_release() -> None:
    source = _source(CURRENT)
    case = source["testCases"][0]
    case["expectationsData"] = None
    case["metricsData"][0]["expectationsData"] = None
    source["datasetVersion"] = "PRIVATE_CANARY"
    report = _account(source)
    assert report["source"]["producer_version"] is None
    assert "PRIVATE_CANARY" not in json.dumps(report)


@pytest.mark.parametrize(
    ("mutation", "location"),
    [
        ("case_missing", "/testCases/0/metricsData/0/flaky"),
        ("metric_missing", "/testCases/0/metricsData/0/flaky"),
        ("next_case_missing", "/testCases/1/flaky"),
    ],
)
def test_mixed_field_eras_refuse_without_trying_a_second_rule(mutation: str, location: str) -> None:
    source = _source(CURRENT)
    case = source["testCases"][0]
    if mutation == "case_missing":
        del case["flaky"]
    elif mutation == "metric_missing":
        del case["metricsData"][0]["flaky"]
    else:
        later = deepcopy(case)
        later["name"] = "synthetic-later"
        del later["flaky"]
        source["testCases"].append(later)
    with pytest.raises(DeepevalAccountingError) as caught:
        _account(source)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "UNSUPPORTED_PROFILE",
        "DEEPEVAL_FLAKY_FIELDS_MIXED",
        location,
    )


@pytest.mark.parametrize("where", ["case", "metric"])
@pytest.mark.parametrize("invalid", [None, 0, "false"])
def test_malformed_flaky_flags_do_not_coerce(where: str, invalid: object) -> None:
    source = _source(CURRENT)
    selected = (
        source["testCases"][0] if where == "case" else source["testCases"][0]["metricsData"][0]
    )
    selected["flaky"] = invalid
    with pytest.raises(DeepevalAccountingError) as caught:
        _account(source)
    pointer = "/testCases/0" + ("/metricsData/0" if where == "metric" else "") + "/flaky"
    assert (caught.value.reason, caught.value.location) == (
        "DEEPEVAL_FLAKY_FLAG_INVALID",
        pointer,
    )


def test_trace_index_continues_selected_steps_with_unexamined_expectations() -> None:
    source = _source(RECOVERY)
    baseline = index_deepeval_trace(source)
    source["testCases"][0]["expectationsData"] = {"reason": "PRIVATE_CANARY"}
    source["testCases"][0]["metricsData"][0]["expectationsData"] = {"reason": "PRIVATE_CANARY"}
    source["datasetVersion"] = "PRIVATE_CANARY"
    selected = cast(dict[str, Any], index_deepeval_compatible(source))
    assert selected["reader_profile"] == TRACE_READER
    assert selected["counts"] == baseline["counts"]
    assert selected["cases"] == baseline["cases"]
    assert "EXPECTATIONS_NOT_ANALYZED" in selected["limitations"]
    assert "SOURCE_CASE_SUCCESS_UNINTERPRETED" in selected["limitations"]
    assert "PRIVATE_CANARY" not in json.dumps(selected)
    assert selected["source"]["producer_version"] is None


@pytest.mark.parametrize(
    ("kind", "code", "location"),
    [
        ("cases", "INPUT_LIMIT_EXCEEDED", "/testCases"),
        ("metrics", "INPUT_LIMIT_EXCEEDED", "/testCases/0/metricsData"),
    ],
)
def test_populations_are_bounded_before_field_scans(kind: str, code: str, location: str) -> None:
    source: dict[str, object] = (
        {"testCases": [{}] * (MAX_CASES + 1)}
        if kind == "cases"
        else {"testCases": [{"metricsData": [{}] * (MAX_METRICS + 1)}]}
    )
    with pytest.raises(DeepevalAccountingError) as caught:
        _account(source)
    assert (caught.value.code, caught.value.location) == (code, location)
