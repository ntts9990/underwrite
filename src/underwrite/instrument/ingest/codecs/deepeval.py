"""Read the explicitly selected DeepEval 4.1.1 TestRun; scores stay source claims.

Closed-key checks cover the run, testCases[*] and metricsData[*]. The
conversationalTestCases and trace subtrees are preserved as received, unchecked;
the capture exercises neither."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

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


_SHAPE = Shape(DeepevalPayloadError, "DEEPEVAL")


def _array(value: object, reason: str) -> list[object]:
    # The two arrays name themselves in full (..._ARRAY_REQUIRED), not "<kind>: <detail>".
    if not is_list(value):
        raise DeepevalPayloadError(reason)
    return value


def _number(payload: dict[str, object], key: str) -> None:
    # 4.1.1 serializes every float field as a float (1 -> 1.0); an int is not its output.
    if type(payload.get(key)) is not float:
        _SHAPE.fail("NUMBER_REQUIRED", key)


def _metric(value: object) -> None:
    metric = _SHAPE.as_closed_object(value, _METRIC_KEYS, "metric")
    _SHAPE.identifier(metric, "name")
    _number(metric, "threshold")
    _SHAPE.flag(metric, "success")
    # 4.1.1 writes exactly one of these: an evaluated score or a producer error.
    # Neither present is an unscored metric and is preserved as such, not filled in.
    if "score" in metric and "error" in metric:
        raise DeepevalPayloadError("DEEPEVAL_METRIC_SCORE_AND_ERROR")
    if "score" in metric:
        _number(metric, "score")
    if "error" in metric:
        _SHAPE.text(metric, "error")


def _test_case(value: object) -> None:
    case = _SHAPE.as_closed_object(value, _TEST_CASE_KEYS, "test case")
    _SHAPE.identifier(case, "name")
    _SHAPE.text(case, "input")
    if "success" in case:
        _SHAPE.flag(case, "success")
    for item in _array(case.get("metricsData", []), "DEEPEVAL_METRICS_ARRAY_REQUIRED"):
        _metric(item)


def read_test_run(value: object) -> Projection:
    """Preserve one pinned TestRun; scored, errored and absent metrics stay distinct."""
    payload = _SHAPE.as_closed_object(value, _TEST_RUN_KEYS, "test run")
    for item in _array(payload.get("testCases"), "DEEPEVAL_TEST_CASES_ARRAY_REQUIRED"):
        _test_case(item)
    return Projection("eval_run", payload)


CODECS: Mapping[tuple[str, str], Codec] = MappingProxyType(
    {("deepeval.test-run", "4.1.1"): read_test_run}
)
