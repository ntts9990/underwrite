"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from _external_formats import ingestor, receive, without
from _repo_paths import repo_root

from underwrite.instrument.ingest.codecs.promptfoo import (
    CODECS,
    PromptfooPayloadError,
    read_eval_output,
)
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor, UnsupportedFormat

FORMAT = "promptfoo.eval-output"
VERSION = "0.123.0"
SUMMARY_VERSION = 3  # EvaluateSummaryV3.version at the tag
ROOT = repo_root(Path(__file__))
GOLDEN = ROOT / "fixtures/golden/external/promptfoo"
OUTPUT = GOLDEN / "results.json"
SOURCE = Source(FORMAT, VERSION, "urn:test:promptfoo")

# Read the capture once: every malformed case below is the real file with one change.
OUTPUT_JSON = cast(dict[str, object], json.loads(OUTPUT.read_bytes()))
METADATA = cast(dict[str, object], OUTPUT_JSON["metadata"])
SUMMARY = cast(dict[str, object], OUTPUT_JSON["results"])
ROWS = cast(list[dict[str, object]], SUMMARY["results"])
GRADING = cast(dict[str, object], ROWS[0]["gradingResult"])
PROMPT = cast(list[dict[str, object]], SUMMARY["prompts"])[0]
METRICS = cast(dict[str, object], PROMPT["metrics"])
STATS = cast(dict[str, object], SUMMARY["stats"])


def _ingestor() -> Ingestor:
    return ingestor(CODECS)


def _receive(payload: object) -> dict[str, object]:
    return receive(_ingestor(), payload, SOURCE, "captured:http-body-not-a-request")


# One constructor per level of the captured file, each overriding the level below it:
# output > metadata, output > results(summary) > {stats, results[0] > gradingResult,
# prompts[0] > metrics}. A case that *drops* a key uses `_without` on the level's constant.
def _output(**overrides: object) -> dict[str, object]:
    return {**OUTPUT_JSON, **overrides}


def _metadata_output(**overrides: object) -> dict[str, object]:
    return _output(metadata={**METADATA, **overrides})


def _summary_output(**overrides: object) -> dict[str, object]:
    return _output(results={**SUMMARY, **overrides})


def _stats_output(**overrides: object) -> dict[str, object]:
    return _summary_output(stats={**STATS, **overrides})


def _row_output(**overrides: object) -> dict[str, object]:
    return _summary_output(results=[{**ROWS[0], **overrides}])


def _grading_output(**overrides: object) -> dict[str, object]:
    return _row_output(gradingResult={**GRADING, **overrides})


def _prompt_output(**overrides: object) -> dict[str, object]:
    return _summary_output(prompts=[{**PROMPT, **overrides}])


def _metrics_output(**overrides: object) -> dict[str, object]:
    return _prompt_output(metrics={**METRICS, **overrides})


def test_registry_pins_exactly_one_profile() -> None:
    assert dict(CODECS) == {(FORMAT, VERSION): read_eval_output}


def test_fixture_record_binds_the_codec_profile() -> None:
    record = json.loads((GOLDEN / "observations.json").read_text(encoding="utf-8"))
    entry = record["files"][OUTPUT.name]
    assert entry["source"] == {"format": FORMAT, "format_version": VERSION}
    assert record["tool"]["version"] == VERSION


def test_real_capture_payload_is_the_whole_document() -> None:
    # File/body identity, kind, source and raw hash are the matrix's claim for every
    # manifest entry; this reader states only how its codec wraps the whole document.
    raw = OUTPUT.read_bytes()
    ingestor = _ingestor()
    observation = ingestor.file(OUTPUT, SOURCE).observation
    assert observation["payload"] == json.loads(raw)


def test_real_capture_keeps_scores_reasons_and_korean_text_as_written() -> None:
    payload = cast(dict[str, object], _receive(OUTPUT_JSON)["payload"])
    assert payload["shareableUrl"] is None and "version" not in payload
    summary = cast(dict[str, object], payload["results"])
    assert summary["version"] == SUMMARY_VERSION
    rows = cast(list[dict[str, object]], summary["results"])
    assert [(r["success"], r["score"], r["failureReason"]) for r in rows] == [
        (True, 1, 0),
        (False, 0.5, 1),
        (True, 0.75, 0),
    ]
    failed = cast(dict[str, object], rows[1]["gradingResult"])
    assert failed["pass"] is False and "this will not match" in str(failed["reason"])
    assert "대한민국" in json.dumps(rows[0]["vars"], ensure_ascii=False)
    stats = cast(dict[str, object], summary["stats"])
    assert (stats["successes"], stats["failures"], stats["errors"]) == (2, 1, 0)


@pytest.mark.parametrize(
    "selector",
    [
        (FORMAT, "0.122.0"),
        (FORMAT, "latest"),
        ("promptfoo.results", VERSION),
        ("promptfoo", VERSION),
    ],
)
def test_unpinned_selector_is_unsupported(selector: tuple[str, str]) -> None:
    with pytest.raises(UnsupportedFormat):
        _ingestor().file(OUTPUT, Source(selector[0], selector[1], "urn:test"))


def test_errored_row_without_grading_is_a_row() -> None:
    errored = _row_output(success=False, score=0, failureReason=2, error="boom", gradingResult=None)
    payload = cast(dict[str, object], _receive(errored)["payload"])
    rows = cast(list[dict[str, object]], cast(dict[str, object], payload["results"])["results"])
    assert rows[0]["error"] == "boom" and rows[0]["gradingResult"] is None


# Every serialized field of the tag's models with a type-plausible value, listed here
# independently of the codec so a typo in its closed sets rejects this file.
FULL_METADATA: dict[str, object] = {**METADATA, "author": "capture"}
FULL_COMPONENT: dict[str, object] = {
    "pass": True,
    "score": 1,
    "reason": "ok",
    "namedScores": {},
    "namedScoreWeights": {},
    "tokensUsed": {"total": 0},
    "componentResults": [],
    "assertion": {"type": "contains", "value": "x"},
    "comment": "c",
    "suggestions": [],
    "metadata": {},
}
FULL_GRADING: dict[str, object] = {
    **GRADING,
    **FULL_COMPONENT,
    "componentResults": [FULL_COMPONENT],
}
FULL_ROW: dict[str, object] = {
    **ROWS[0],
    "description": "d",
    "error": None,
    "incurredCost": 0,
    "evaluationId": "eval-1",
    "traceId": "trace-1",
    "gradingResult": FULL_GRADING,
}
FULL_METRICS: dict[str, object] = {
    **METRICS,
    "redteam": {
        "pluginPassCount": {},
        "pluginFailCount": {},
        "strategyPassCount": {},
        "strategyFailCount": {},
    },
    "incurredCost": 0,
}
FULL_PROMPT: dict[str, object] = {
    **PROMPT,
    "template": "t",
    "display": "d",
    "function": "f",
    "config": {},
    "metrics": FULL_METRICS,
}
FULL_STATS: dict[str, object] = {**STATS, "generationDurationMs": 1}
FULL_OUTPUT: dict[str, object] = {
    **OUTPUT_JSON,
    "metadata": FULL_METADATA,
    "results": {**SUMMARY, "results": [FULL_ROW], "prompts": [FULL_PROMPT], "stats": FULL_STATS},
    "traces": [],
    "blobAssets": [],
}


def test_output_with_every_tag_field_is_accepted_as_written() -> None:
    assert _receive(FULL_OUTPUT)["payload"] == FULL_OUTPUT


MALFORMED: dict[str, tuple[object, str]] = {
    "root_not_object": ([], "PROMPTFOO_OBJECT_REQUIRED: output"),
    "root_unknown_field": (_output(version=3), "PROMPTFOO_UNKNOWN_FIELD: output"),
    "eval_id_missing": (without(_output(), "evalId"), "PROMPTFOO_TEXT_OR_NULL_REQUIRED: evalId"),
    "eval_id_number": (_output(evalId=1), "PROMPTFOO_TEXT_OR_NULL_REQUIRED: evalId"),
    "shareable_url_missing": (
        without(_output(), "shareableUrl"),
        "PROMPTFOO_TEXT_OR_NULL_REQUIRED: shareableUrl",
    ),
    "metadata_missing": (without(_output(), "metadata"), "PROMPTFOO_OBJECT_REQUIRED: metadata"),
    "metadata_unknown_field": (_metadata_output(hostname="x"), "PROMPTFOO_UNKNOWN_FIELD: metadata"),
    "producer_version_other": (
        _metadata_output(promptfooVersion="0.124.0"),
        "PROMPTFOO_PRODUCER_VERSION_MISMATCH",
    ),
    "producer_version_missing": (
        _output(metadata=without(METADATA, "promptfooVersion")),
        "PROMPTFOO_PRODUCER_VERSION_MISMATCH",
    ),
    "config_not_object": (_output(config=[]), "PROMPTFOO_OBJECT_REQUIRED: config"),
    "summary_missing": (without(_output(), "results"), "PROMPTFOO_OBJECT_REQUIRED: results"),
    "summary_unknown_field": (_summary_output(table={}), "PROMPTFOO_UNKNOWN_FIELD: results"),
    "summary_version_two": (_summary_output(version=2), "PROMPTFOO_SUMMARY_VERSION_UNSUPPORTED"),
    "summary_real_v2_shape": (
        _summary_output(version=2, table={"head": {}, "body": []}),
        "PROMPTFOO_SUMMARY_VERSION_UNSUPPORTED",
    ),
    "summary_version_text": (_summary_output(version="3"), "PROMPTFOO_SUMMARY_VERSION_UNSUPPORTED"),
    "summary_version_flag": (
        _summary_output(version=True),
        "PROMPTFOO_SUMMARY_VERSION_UNSUPPORTED",
    ),
    "summary_timestamp_blank": (
        _summary_output(timestamp=" "),
        "PROMPTFOO_IDENTIFIER_REQUIRED: results timestamp",
    ),
    "rows_not_array": (_summary_output(results={}), "PROMPTFOO_ARRAY_REQUIRED: results rows"),
    "row_not_object": (_summary_output(results=["x"]), "PROMPTFOO_OBJECT_REQUIRED: result 0"),
    "row_unknown_field": (_row_output(judge="x"), "PROMPTFOO_UNKNOWN_FIELD: result 0"),
    "row_prompt_idx_text": (
        _row_output(promptIdx="0"),
        "PROMPTFOO_COUNT_REQUIRED: result 0 promptIdx",
    ),
    "row_test_idx_flag": (_row_output(testIdx=False), "PROMPTFOO_COUNT_REQUIRED: result 0 testIdx"),
    "row_prompt_id_blank": (
        _row_output(promptId=""),
        "PROMPTFOO_IDENTIFIER_REQUIRED: result 0 promptId",
    ),
    "row_success_number": (_row_output(success=1), "PROMPTFOO_FLAG_REQUIRED: result 0 success"),
    "row_score_text": (_row_output(score="1"), "PROMPTFOO_NUMBER_REQUIRED: result 0 score"),
    "row_score_flag": (_row_output(score=True), "PROMPTFOO_NUMBER_REQUIRED: result 0 score"),
    "row_latency_missing": (
        _summary_output(results=[without(ROWS[0], "latencyMs")]),
        "PROMPTFOO_NUMBER_REQUIRED: result 0 latencyMs",
    ),
    "row_failure_reason_three": (
        _row_output(failureReason=3),
        "PROMPTFOO_FAILURE_REASON_UNSUPPORTED: result 0",
    ),
    "row_failure_reason_flag": (
        _row_output(failureReason=True),
        "PROMPTFOO_FAILURE_REASON_UNSUPPORTED: result 0",
    ),
    "row_failure_reason_text": (
        _row_output(failureReason="ASSERT"),
        "PROMPTFOO_FAILURE_REASON_UNSUPPORTED: result 0",
    ),
    "row_named_scores_list": (
        _row_output(namedScores=[]),
        "PROMPTFOO_OBJECT_REQUIRED: result 0 namedScores",
    ),
    "row_error_number": (_row_output(error=1), "PROMPTFOO_TEXT_OR_NULL_REQUIRED: result 0 error"),
    "grading_not_object": (
        _row_output(gradingResult="pass"),
        "PROMPTFOO_OBJECT_REQUIRED: result 0 grading",
    ),
    "grading_unknown_field": (
        _grading_output(verdict="x"),
        "PROMPTFOO_UNKNOWN_FIELD: result 0 grading",
    ),
    "grading_pass_missing": (
        _row_output(gradingResult=without(GRADING, "pass")),
        "PROMPTFOO_FLAG_REQUIRED: result 0 grading pass",
    ),
    "grading_score_text": (
        _grading_output(score="1"),
        "PROMPTFOO_NUMBER_REQUIRED: result 0 grading score",
    ),
    "grading_reason_null": (
        _grading_output(reason=None),
        "PROMPTFOO_TEXT_REQUIRED: result 0 grading reason",
    ),
    "grading_components_not_array": (
        _grading_output(componentResults={}),
        "PROMPTFOO_ARRAY_REQUIRED: result 0 grading components",
    ),
    "grading_component_pass_text": (
        _grading_output(componentResults=[{"pass": "yes", "score": 1, "reason": "ok"}]),
        "PROMPTFOO_FLAG_REQUIRED: result 0 grading component 0 pass",
    ),
    "prompts_not_array": (_summary_output(prompts={}), "PROMPTFOO_ARRAY_REQUIRED: prompts"),
    "prompt_not_object": (_summary_output(prompts=["x"]), "PROMPTFOO_OBJECT_REQUIRED: prompt 0"),
    "prompt_unknown_field": (_prompt_output(model="x"), "PROMPTFOO_UNKNOWN_FIELD: prompt 0"),
    "prompt_raw_missing": (
        _summary_output(prompts=[without(PROMPT, "raw")]),
        "PROMPTFOO_TEXT_REQUIRED: prompt 0 raw",
    ),
    "prompt_provider_number": (
        _prompt_output(provider=1),
        "PROMPTFOO_TEXT_REQUIRED: prompt 0 provider",
    ),
    "prompt_metrics_unknown_field": (
        _metrics_output(rank=1),
        "PROMPTFOO_UNKNOWN_FIELD: prompt 0 metrics",
    ),
    "prompt_metrics_score_text": (
        _metrics_output(score="2"),
        "PROMPTFOO_NUMBER_REQUIRED: prompt 0 metrics score",
    ),
    "prompt_metrics_pass_count_float": (
        _metrics_output(testPassCount=2.0),
        "PROMPTFOO_COUNT_REQUIRED: prompt 0 metrics testPassCount",
    ),
    "stats_missing": (
        _output(results=without(SUMMARY, "stats")),
        "PROMPTFOO_OBJECT_REQUIRED: stats",
    ),
    "stats_unknown_field": (_stats_output(skipped=0), "PROMPTFOO_UNKNOWN_FIELD: stats"),
    "stats_errors_text": (_stats_output(errors="0"), "PROMPTFOO_COUNT_REQUIRED: stats errors"),
}


@pytest.mark.parametrize("fault", sorted(MALFORMED))
def test_malformed_or_drifted_shape_fails_closed(fault: str) -> None:
    payload, reason = MALFORMED[fault]
    with pytest.raises(PromptfooPayloadError) as caught:
        CODECS[(FORMAT, VERSION)](payload)
    assert caught.value.args == (reason,)
