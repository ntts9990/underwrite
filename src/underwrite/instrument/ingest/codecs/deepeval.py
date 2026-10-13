"""Read explicitly selected DeepEval 4.1.1 and 4.2.8 TestRuns; scores stay source claims.

Closed-key checks cover the run, testCases[*] and metricsData[*]. The
conversationalTestCases and trace subtrees are preserved as received, unchecked;
the capture exercises neither."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from underwrite.instrument.ingest.codecs._shape import Shape, is_list
from underwrite.instrument.ingest.project import Projection
from underwrite.instrument.ingest.transport import Codec


class DeepevalPayloadError(ValueError):
    """A selected DeepEval TestRun lacks the pinned minimal consumable structure."""


# Alias keys that deepeval==4.1.1 serializes with
# `TestRun.model_dump(by_alias=True, exclude_none=True)` (tag v4.1.1, commit
# 58c9ef78a4634ba119c7d2cc145f5cf9aeb24524: deepeval/test_run/test_run.py,
# deepeval/test_run/api.py, deepeval/tracing/api.py). The file declares no
# version of its own, so an unknown key is the only observable drift signal.
_TEST_RUN_KEYS = frozenset(
    {
        "testFile",
        "testCases",
        "conversationalTestCases",
        "metricsScores",
        "traceMetricsScores",
        "identifier",
        "hyperparameters",
        "prompts",
        "testPassed",
        "testFailed",
        "runDuration",
        "evaluationCost",
        "datasetAlias",
        "datasetId",
        "official",
    }
)
_TEST_CASE_KEYS = frozenset(
    {
        "name",
        "input",
        "actualOutput",
        "expectedOutput",
        "context",
        "retrievalContext",
        "toolsCalled",
        "expectedTools",
        "tokenCost",
        "completionTime",
        "tags",
        "imagesMapping",
        "success",
        "metricsData",
        "runDuration",
        "evaluationCost",
        "order",
        "metadata",
        "comments",
        "trace",
    }
)
_METRIC_KEYS = frozenset(
    {
        "name",
        "threshold",
        "success",
        "score",
        "reason",
        "strictMode",
        "evaluationModel",
        "error",
        "evaluationCost",
        "inputTokenCount",
        "outputTokenCount",
        "verboseLogs",
    }
)


_Profile = Literal["4.1.1", "4.2.8"]
_TEST_CASE_KEYS_V4_2_8 = _TEST_CASE_KEYS | {"flaky"}
_METRIC_KEYS_V4_2_8 = _METRIC_KEYS | {"flaky"}


_SHAPE = Shape(DeepevalPayloadError, "DEEPEVAL")


def _array(value: object, reason: str) -> list[object]:
    # The two arrays name themselves in full (..._ARRAY_REQUIRED), not "<kind>: <detail>".
    if not is_list(value):
        raise DeepevalPayloadError(reason)
    return value


def _metric(value: object, profile: _Profile) -> None:
    keys = _METRIC_KEYS if profile == "4.1.1" else _METRIC_KEYS_V4_2_8
    metric = _SHAPE.as_closed_object(value, keys, "metric")
    _SHAPE.identifier(metric, "name")
    if profile == "4.1.1" or "threshold" in metric:
        _SHAPE.number(metric, "threshold")
    if profile == "4.1.1" or "success" in metric:
        _SHAPE.flag(metric, "success")
    if profile == "4.2.8":
        _SHAPE.flag(metric, "flaky")
    # 4.1.1 writes exactly one of these: an evaluated score or a producer error.
    # Neither present is an unscored metric and is preserved as such, not filled in.
    if "score" in metric and "error" in metric:
        raise DeepevalPayloadError("DEEPEVAL_METRIC_SCORE_AND_ERROR")
    if "score" in metric:
        _SHAPE.number(metric, "score")
    if "error" in metric:
        _SHAPE.text(metric, "error")


def _test_case(value: object, profile: _Profile) -> None:
    keys = _TEST_CASE_KEYS if profile == "4.1.1" else _TEST_CASE_KEYS_V4_2_8
    case = _SHAPE.as_closed_object(value, keys, "test case")
    _SHAPE.identifier(case, "name")
    _SHAPE.text(case, "input")
    if "success" in case:
        _SHAPE.flag(case, "success")
    if profile == "4.2.8":
        _SHAPE.flag(case, "flaky")
    for item in _array(case.get("metricsData", []), "DEEPEVAL_METRICS_ARRAY_REQUIRED"):
        _metric(item, profile)


def _read_test_run(value: object, profile: _Profile) -> Projection:
    payload = _SHAPE.as_closed_object(value, _TEST_RUN_KEYS, "test run")
    for item in _array(payload.get("testCases"), "DEEPEVAL_TEST_CASES_ARRAY_REQUIRED"):
        _test_case(item, profile)
    return Projection("eval_run", payload)


def read_test_run(value: object) -> Projection:
    """Preserve the pinned 4.1.1 TestRun with its original required metric fields."""
    return _read_test_run(value, "4.1.1")


def read_test_run_v4_2_8(value: object) -> Projection:
    """Preserve the pinned 4.2.8 TestRun, including flaky and absent metric verdicts."""
    return _read_test_run(value, "4.2.8")


CODECS: Mapping[tuple[str, str], Codec] = MappingProxyType(
    {
        ("deepeval.test-run", "4.1.1"): read_test_run,
        ("deepeval.test-run", "4.2.8"): read_test_run_v4_2_8,
    }
)
