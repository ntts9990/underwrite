"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from _external_formats import ingestor, receive, without
from _repo_paths import repo_root

from underwrite.instrument.ingest import formats
from underwrite.instrument.ingest.codecs.deepeval import (
    CODECS,
    DeepevalPayloadError,
    read_test_run,
)
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor, UnsupportedFormat

ROOT = repo_root(Path(__file__))
GOLDEN = ROOT / "fixtures/golden/external/deepeval"
CAPTURE = GOLDEN / "test_run_20260914_132634.json"
FORMAT = "deepeval.test-run"
VERSION = "4.1.1"
SOURCE = Source(FORMAT, VERSION, "urn:test:deepeval")


def _ingestor() -> Ingestor:
    return ingestor(CODECS, max_bytes=65536, max_depth=32)


def _receive(payload: object) -> dict[str, object]:
    return receive(_ingestor(), payload, SOURCE, "in-memory:test")


def _metric(**overrides: object) -> dict[str, object]:
    metric: dict[str, object] = {
        "name": "Exact Match",
        "threshold": 0.5,
        "success": True,
        "score": 1.0,
    }
    metric.update(overrides)
    return metric


def _case(**overrides: object) -> dict[str, object]:
    case: dict[str, object] = {
        "name": "test_one",
        "input": "2+2",
        "success": True,
        "metricsData": [_metric()],
    }
    case.update(overrides)
    return case


def _run(**overrides: object) -> dict[str, object]:
    run: dict[str, object] = {"testCases": [_case()]}
    run.update(overrides)
    return run


def test_registry_contains_only_the_pinned_profile() -> None:
    assert dict(CODECS) == {(FORMAT, VERSION): read_test_run}


def test_registered_profile_cannot_be_replaced() -> None:
    registry = cast(dict[tuple[str, str], object], CODECS)
    with pytest.raises(TypeError):
        registry[(FORMAT, VERSION)] = read_test_run


def test_duplicate_external_selector_is_a_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(formats.deepeval, "CODECS", formats.evidence_bundle.CODECS)
    with pytest.raises(formats.CodecConfigurationError, match="^DUPLICATE_CODEC_SELECTOR$"):
        formats.trusted_codecs()


def test_fixture_record_binds_the_codec_profile() -> None:
    record = json.loads((GOLDEN / "observations.json").read_text(encoding="utf-8"))
    entry = record["files"][CAPTURE.name]
    assert entry["source"] == {"format": FORMAT, "format_version": VERSION}
    assert record["tool"]["version"] == VERSION


def test_real_capture_payload_is_the_whole_document() -> None:
    # File/body identity, kind, source and raw hash are the matrix's claim for every
    # manifest entry; this reader states only how its codec wraps the whole document.
    raw = CAPTURE.read_bytes()
    ingestor = _ingestor()
    observation = ingestor.file(CAPTURE, SOURCE).observation
    assert observation["payload"] == json.loads(raw)


def test_real_capture_keeps_scored_and_errored_metrics_distinct() -> None:
    payload = cast(dict[str, object], _ingestor().file(CAPTURE, SOURCE).observation["payload"])
    cases = cast(list[dict[str, object]], payload["testCases"])
    metrics = {
        (case["name"], metric["name"]): metric
        for case in cases
        for metric in cast(list[dict[str, object]], case["metricsData"])
    }
    scored = metrics[("test_observed_fail", "Exact Match")]
    errored = metrics[("test_error_and_observed", "Always Errors")]
    assert scored["score"] == 0.0 and "error" not in scored
    assert "score" not in errored and isinstance(errored["error"], str)
    assert ("test_observed_pass", "Always Errors") not in metrics


@pytest.mark.parametrize(
    "selector",
    [(FORMAT, "4.2.2"), (FORMAT, "unknown"), (FORMAT, "latest"), ("deepeval", VERSION)],
)
def test_unpinned_selector_is_unsupported(selector: tuple[str, str]) -> None:
    source = Source(selector[0], selector[1], "urn:test")
    with pytest.raises(UnsupportedFormat, match="^UNSUPPORTED_FORMAT$"):
        _ingestor().file(CAPTURE, source)


def test_unscored_metric_is_preserved_not_filled_in() -> None:
    unscored = _metric(success=False)
    del unscored["score"]
    run = _run(testCases=[_case(metricsData=[unscored])])
    assert _receive(run)["payload"] == run


def test_empty_run_and_case_without_optional_fields_are_accepted() -> None:
    assert _receive({"testCases": []})["payload"] == {"testCases": []}
    minimal = {"testCases": [{"name": "test_min", "input": ""}]}
    assert _receive(minimal)["payload"] == minimal


def test_every_pinned_optional_key_is_accepted() -> None:
    run = _run(
        testFile="sample_eval.py",
        conversationalTestCases=[],
        metricsScores=[],
        traceMetricsScores={},
        identifier="id",
        hyperparameters={},
        prompts=[],
        testPassed=1,
        testFailed=0,
        runDuration=0.1,
        evaluationCost=0.0,
        datasetAlias="a",
        datasetId="d",
        official=False,
        testCases=[
            _case(
                actualOutput="4",
                expectedOutput="4",
                context=[],
                retrievalContext=[],
                toolsCalled=[],
                expectedTools=[],
                tokenCost=0.0,
                completionTime=0.0,
                tags=[],
                imagesMapping={},
                runDuration=0.0,
                evaluationCost=0.0,
                order=0,
                metadata={},
                comments="",
                trace={},
                metricsData=[
                    _metric(
                        reason="r",
                        strictMode=False,
                        evaluationModel="n/a",
                        evaluationCost=0.0,
                        inputTokenCount=0,
                        outputTokenCount=0,
                        verboseLogs="",
                    )
                ],
            )
        ],
    )
    assert _receive(run)["payload"] == run


MALFORMED: dict[str, tuple[object, str]] = {
    "root_not_object": ([], "DEEPEVAL_OBJECT_REQUIRED: test run"),
    "main_only_top_level_field": (_run(schemaVersion=2), "DEEPEVAL_UNKNOWN_FIELD: test run"),
    "test_cases_missing": ({}, "DEEPEVAL_TEST_CASES_ARRAY_REQUIRED"),
    "test_cases_not_array": (_run(testCases={}), "DEEPEVAL_TEST_CASES_ARRAY_REQUIRED"),
    "case_not_object": (_run(testCases=["x"]), "DEEPEVAL_OBJECT_REQUIRED: test case"),
    "case_unknown_field": (_run(testCases=[_case(id="x")]), "DEEPEVAL_UNKNOWN_FIELD: test case"),
    "case_name_missing": (
        _run(testCases=[without(_case(), "name")]),
        "DEEPEVAL_IDENTIFIER_REQUIRED: name",
    ),
    "case_name_blank": (_run(testCases=[_case(name=" ")]), "DEEPEVAL_IDENTIFIER_REQUIRED: name"),
    "case_input_missing": (
        _run(testCases=[without(_case(), "input")]),
        "DEEPEVAL_TEXT_REQUIRED: input",
    ),
    "case_input_not_text": (_run(testCases=[_case(input=1)]), "DEEPEVAL_TEXT_REQUIRED: input"),
    "case_success_not_flag": (
        _run(testCases=[_case(success=1)]),
        "DEEPEVAL_FLAG_REQUIRED: success",
    ),
    "metrics_not_array": (
        _run(testCases=[_case(metricsData={})]),
        "DEEPEVAL_METRICS_ARRAY_REQUIRED",
    ),
    "metric_not_object": (
        _run(testCases=[_case(metricsData=[1])]),
        "DEEPEVAL_OBJECT_REQUIRED: metric",
    ),
    "metric_unknown_field": (
        _run(testCases=[_case(metricsData=[_metric(scoreBreakdown={})])]),
        "DEEPEVAL_UNKNOWN_FIELD: metric",
    ),
    "metric_name_missing": (
        _run(testCases=[_case(metricsData=[without(_metric(), "name")])]),
        "DEEPEVAL_IDENTIFIER_REQUIRED: name",
    ),
    "metric_threshold_missing": (
        _run(testCases=[_case(metricsData=[without(_metric(), "threshold")])]),
        "DEEPEVAL_NUMBER_REQUIRED: threshold",
    ),
    "metric_threshold_int": (
        _run(testCases=[_case(metricsData=[_metric(threshold=1)])]),
        "DEEPEVAL_NUMBER_REQUIRED: threshold",
    ),
    "metric_threshold_flag": (
        _run(testCases=[_case(metricsData=[_metric(threshold=True)])]),
        "DEEPEVAL_NUMBER_REQUIRED: threshold",
    ),
    "metric_success_missing": (
        _run(testCases=[_case(metricsData=[without(_metric(), "success")])]),
        "DEEPEVAL_FLAG_REQUIRED: success",
    ),
    "metric_score_text": (
        _run(testCases=[_case(metricsData=[_metric(score="1.0")])]),
        "DEEPEVAL_NUMBER_REQUIRED: score",
    ),
    "metric_score_int": (
        _run(testCases=[_case(metricsData=[_metric(score=1)])]),
        "DEEPEVAL_NUMBER_REQUIRED: score",
    ),
    "metric_score_flag": (
        _run(testCases=[_case(metricsData=[_metric(score=True)])]),
        "DEEPEVAL_NUMBER_REQUIRED: score",
    ),
    "metric_error_not_text": (
        _run(testCases=[_case(metricsData=[without(_metric(error=1), "score")])]),
        "DEEPEVAL_TEXT_REQUIRED: error",
    ),
    "metric_error_with_null_score": (
        _run(testCases=[_case(metricsData=[_metric(error=1, score=None)])]),
        "DEEPEVAL_METRIC_SCORE_AND_ERROR",
    ),
    "metric_score_and_error": (
        _run(testCases=[_case(metricsData=[_metric(error="boom")])]),
        "DEEPEVAL_METRIC_SCORE_AND_ERROR",
    ),
}


@pytest.mark.parametrize("fault", sorted(MALFORMED))
def test_malformed_or_drifted_shape_fails_closed(fault: str) -> None:
    payload, reason = MALFORMED[fault]
    with pytest.raises(DeepevalPayloadError) as caught:
        read_test_run(payload)
    assert caught.value.args == (reason,)
