"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from _external_formats import ingestor, receive, without
from _repo_paths import repo_root

from underwrite.instrument.ingest.codecs.inspect import (
    CODECS,
    InspectPayloadError,
    read_eval_log,
)
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor, UnsupportedFormat

FORMAT = "inspect.eval-log"
VERSION = "0.3.263"
LOG_VERSION = 2  # EvalLog.version at the tag
ROOT = repo_root(Path(__file__))
GOLDEN = ROOT / "fixtures/golden/external/inspect"
LOG = GOLDEN / "2026-09-15T01-07-55-00-00_underwrite-capture_WaX6koaYGQoZ7VpAhPpEN8.json"
SOURCE = Source(FORMAT, VERSION, "urn:test:inspect")

# Read the capture once: every malformed case below is the real log with one change.
LOG_JSON = cast(dict[str, object], json.loads(LOG.read_bytes()))
SPEC = cast(dict[str, object], LOG_JSON["eval"])
STATS = cast(dict[str, object], LOG_JSON["stats"])
RESULTS = cast(dict[str, object], LOG_JSON["results"])
RESULT_SCORE = cast(list[dict[str, object]], RESULTS["scores"])[0]
SAMPLE = cast(list[dict[str, object]], LOG_JSON["samples"])[0]


def _ingestor() -> Ingestor:
    return ingestor(CODECS)


def _receive(payload: object) -> dict[str, object]:
    return receive(_ingestor(), payload, SOURCE, "captured:http-body-not-a-request")


def _log(**overrides: object) -> dict[str, object]:
    return {**LOG_JSON, **overrides}


@pytest.mark.parametrize("status", [[], {}, ["success"]])
def test_unhashable_status_has_typed_error(status: object) -> None:
    with pytest.raises(InspectPayloadError, match="INSPECT_STATUS_UNSUPPORTED"):
        _receive(_log(status=status))


def _results(**overrides: object) -> dict[str, object]:
    return {**RESULTS, **overrides}


def _spec_log(**overrides: object) -> dict[str, object]:
    return _log(eval={**SPEC, **overrides})


def _score_log(**overrides: object) -> dict[str, object]:
    """The log whose single *results* score carries the change; a sample score is not one."""
    return _log(results=_results(scores=[{**RESULT_SCORE, **overrides}]))


def _sample_log(**overrides: object) -> dict[str, object]:
    return _log(samples=[{**SAMPLE, **overrides}])


def test_registry_pins_exactly_one_profile() -> None:
    assert dict(CODECS) == {(FORMAT, VERSION): read_eval_log}


def test_fixture_record_binds_the_codec_profile() -> None:
    record = json.loads((GOLDEN / "observations.json").read_text(encoding="utf-8"))
    entry = record["files"][LOG.name]
    assert entry["source"] == {"format": FORMAT, "format_version": VERSION}
    assert record["tool"]["version"] == VERSION


def test_real_capture_payload_is_the_whole_document() -> None:
    # File/body identity, kind, source and raw hash are the matrix's claim for every
    # manifest entry; this reader states only how its codec wraps the whole document.
    raw = LOG.read_bytes()
    ingestor = _ingestor()
    observation = ingestor.file(LOG, SOURCE).observation
    assert observation["payload"] == json.loads(raw)


def test_real_capture_keeps_scores_metrics_and_korean_text_as_written() -> None:
    payload = cast(dict[str, object], _receive(LOG_JSON)["payload"])
    assert payload["version"] == LOG_VERSION and payload["status"] == "success"
    spec = cast(dict[str, object], payload["eval"])
    assert spec["model"] == "mockllm/model" and spec["packages"] == {"inspect_ai": VERSION}
    samples = cast(list[dict[str, object]], payload["samples"])
    scores = {str(s["id"]): cast(dict[str, dict[str, object]], s["scores"]) for s in samples}
    assert [scores[i]["includes"]["value"] for i in ("s1-default", "s2-korean", "s3-partial")] == [
        "C",
        "I",
        "C",
    ]
    assert "대한민국" in json.dumps(samples[1]["input"], ensure_ascii=False)
    results = cast(dict[str, object], payload["results"])
    metrics = cast(list[dict[str, object]], results["scores"])[0]["metrics"]
    accuracy = cast(dict[str, dict[str, object]], metrics)["accuracy"]["value"]
    assert accuracy == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    "selector",
    [(FORMAT, "0.3.262"), (FORMAT, "latest"), ("inspect.eval", VERSION), ("inspect", VERSION)],
)
def test_unpinned_selector_is_unsupported(selector: tuple[str, str]) -> None:
    with pytest.raises(UnsupportedFormat):
        _ingestor().file(LOG, Source(selector[0], selector[1], "urn:test"))


def test_non_finite_number_is_refused_by_the_transport_not_sanitized() -> None:
    body = LOG.read_bytes().replace(b"0.6666666666666666", b"NaN", 1)
    with pytest.raises(ValueError, match="NON_FINITE_JSON_NUMBER"):
        _ingestor().http_body(body, "captured:http-body-not-a-request", SOURCE)


def test_started_log_without_results_or_samples_is_a_log() -> None:
    started = _log(status="started")
    started.pop("results")
    started.pop("samples")
    started.pop("reductions")
    assert cast(dict[str, object], _receive(started)["payload"])["status"] == "started"


@pytest.mark.parametrize("sample_id", [7, "seven"])
def test_int_or_text_sample_id_is_kept(sample_id: object) -> None:
    payload = _receive(_sample_log(id=sample_id))["payload"]
    samples = cast(list[dict[str, object]], cast(dict[str, object], payload)["samples"])
    assert samples[0]["id"] == sample_id


# Every serialized field of the tag's models with a type-plausible value, listed here
# independently of the codec so a typo in its closed sets rejects this log.
FULL_SPEC: dict[str, object] = {
    **SPEC,
    "eval_set_id": "set-1",
    "task_version": 1,
    "task_display_name": "underwrite_capture",
    "task_registry_name": "sample_task/underwrite_capture",
    "task_attribs": {},
    "solver": "generate",
    "solver_args": {},
    "solver_args_passed": {},
    "tags": ["capture"],
    "sandbox": {"type": "local", "config": None},
    "model_base_url": "http://localhost",
    "model_roles": {},
    "revision": {"type": "git", "origin": "o", "commit": "c", "dirty": False},
    "metrics": [],
    "headline_metric": "accuracy",
    "viewer": {"view": "default"},
}
FULL_RESULT_SCORE: dict[str, object] = {
    **RESULT_SCORE,
    "reducer": "mean",
    "metadata": {},
    "metrics": {
        "accuracy": {"name": "accuracy", "group": "g", "value": 1, "params": {}, "metadata": {}}
    },
}
FULL_RESULTS: dict[str, object] = {
    **RESULTS,
    "logged_samples": 3,
    "early_stopping": {"stopped": False},
    "metadata": {},
    "scores": [FULL_RESULT_SCORE],
}
FULL_SAMPLE: dict[str, object] = {
    **SAMPLE,
    "choices": ["a", "b"],
    "sandbox": {"type": "local", "config": None},
    "files": ["f.txt"],
    "setup": "echo",
    "timelines": [],
    "model_fallbacks": [],
    "invalidation": {"reason": "none"},
    "error": {"message": "m", "traceback": "t", "traceback_ansi": "t"},
    "limit": {"type": "time", "limit": 1},
    "token_limit": 1,
    "token_limit_type": "total",
    "token_limit_usage": 0,
    "message_limit": 1,
    "time_limit": 1,
}
FULL_LOG: dict[str, object] = {
    **LOG_JSON,
    "eval": FULL_SPEC,
    "plan": {
        **cast(dict[str, object], LOG_JSON["plan"]),
        "finish": {"solver": "finish", "params": {}, "params_passed": {}},
    },
    "results": FULL_RESULTS,
    "samples": [FULL_SAMPLE],
    "error": {"message": "m", "traceback": "t", "traceback_ansi": "t"},
    "log_updates": [],
    "config_updates": [],
    "status": "error",
}


def test_log_with_every_tag_field_is_accepted_as_written() -> None:
    payload = _receive(FULL_LOG)["payload"]
    assert payload == FULL_LOG


@pytest.mark.parametrize("key", ["run_id", "task_id"])
def test_blank_run_or_task_id_is_the_models_own_default(key: str) -> None:
    payload = cast(dict[str, object], _receive(_spec_log(**{key: ""}))["payload"])
    assert cast(dict[str, object], payload["eval"])[key] == ""


def test_overflowing_number_literal_is_refused_by_the_transport() -> None:
    body = LOG.read_bytes().replace(b"0.6666666666666666", b"1e400", 1)
    with pytest.raises(ValueError, match="NON_FINITE_JSON_NUMBER"):
        _ingestor().http_body(body, "captured:http-body-not-a-request", SOURCE)


MALFORMED: dict[str, tuple[object, str]] = {
    "root_not_object": ([], "INSPECT_OBJECT_REQUIRED: eval log"),
    "root_unknown_field": (_log(header={}), "INSPECT_UNKNOWN_FIELD: eval log"),
    "version_missing": (without(_log(), "version"), "INSPECT_LOG_VERSION_UNSUPPORTED"),
    "version_three": (_log(version=3), "INSPECT_LOG_VERSION_UNSUPPORTED"),
    "version_text": (_log(version="2"), "INSPECT_LOG_VERSION_UNSUPPORTED"),
    "version_flag": (_log(version=True), "INSPECT_LOG_VERSION_UNSUPPORTED"),
    "status_unknown": (_log(status="running"), "INSPECT_STATUS_UNSUPPORTED"),
    "status_missing": (without(_log(), "status"), "INSPECT_STATUS_UNSUPPORTED"),
    "spec_missing": (without(_log(), "eval"), "INSPECT_OBJECT_REQUIRED: eval"),
    "spec_unknown_field": (_spec_log(task_hash="x"), "INSPECT_UNKNOWN_FIELD: eval"),
    "spec_eval_id_blank": (_spec_log(eval_id=" "), "INSPECT_IDENTIFIER_REQUIRED: eval_id"),
    "spec_run_id_null": (_spec_log(run_id=None), "INSPECT_TEXT_REQUIRED: run_id"),
    "spec_task_id_number": (_spec_log(task_id=7), "INSPECT_TEXT_REQUIRED: task_id"),
    "spec_task_missing": (_log(eval=without(SPEC, "task")), "INSPECT_IDENTIFIER_REQUIRED: task"),
    "spec_model_not_text": (_spec_log(model=1), "INSPECT_IDENTIFIER_REQUIRED: model"),
    "spec_created_null": (_spec_log(created=None), "INSPECT_IDENTIFIER_REQUIRED: created"),
    "plan_not_object": (_log(plan=[]), "INSPECT_OBJECT_REQUIRED: plan"),
    "plan_unknown_field": (
        _log(plan={**cast(dict[str, object], LOG_JSON["plan"]), "solver": "x"}),
        "INSPECT_UNKNOWN_FIELD: plan",
    ),
    "stats_unknown_field": (_log(stats={**STATS, "cost": 0}), "INSPECT_UNKNOWN_FIELD: stats"),
    "results_not_object": (_log(results=[]), "INSPECT_OBJECT_REQUIRED: results"),
    "results_unknown_field": (_log(results=_results(summary={})), "INSPECT_UNKNOWN_FIELD: results"),
    "results_scores_not_array": (
        _log(results=_results(scores={})),
        "INSPECT_ARRAY_REQUIRED: results scores",
    ),
    "results_score_not_object": (
        _log(results=_results(scores=["x"])),
        "INSPECT_OBJECT_REQUIRED: score",
    ),
    "results_score_unknown_field": (_score_log(rank=1), "INSPECT_UNKNOWN_FIELD: score"),
    "results_score_name_blank": (_score_log(name=""), "INSPECT_IDENTIFIER_REQUIRED: name"),
    "results_score_scorer_blank": (_score_log(scorer=" "), "INSPECT_IDENTIFIER_REQUIRED: scorer"),
    "results_score_metrics_not_object": (
        _score_log(metrics=[]),
        "INSPECT_OBJECT_REQUIRED: metrics",
    ),
    "results_metric_not_object": (
        _score_log(metrics={"accuracy": 0.5}),
        "INSPECT_OBJECT_REQUIRED: metric accuracy",
    ),
    "results_metric_unknown_field": (
        _score_log(metrics={"accuracy": {"name": "accuracy", "value": 0.5, "unit": "%"}}),
        "INSPECT_UNKNOWN_FIELD: metric accuracy",
    ),
    "results_metric_value_text": (
        _score_log(metrics={"accuracy": {"name": "accuracy", "value": "0.67", "params": {}}}),
        "INSPECT_NUMBER_REQUIRED: metric accuracy",
    ),
    "results_metric_value_flag": (
        _score_log(metrics={"accuracy": {"name": "accuracy", "value": True}}),
        "INSPECT_NUMBER_REQUIRED: metric accuracy",
    ),
    "samples_not_array": (_log(samples={}), "INSPECT_ARRAY_REQUIRED: samples"),
    "sample_not_object": (_log(samples=["x"]), "INSPECT_OBJECT_REQUIRED: sample"),
    "sample_unknown_field": (_sample_log(judge="x"), "INSPECT_UNKNOWN_FIELD: sample"),
    "sample_id_missing": (_log(samples=[without(SAMPLE, "id")]), "INSPECT_SAMPLE_ID_REQUIRED"),
    "sample_id_blank": (_sample_log(id=""), "INSPECT_SAMPLE_ID_REQUIRED"),
    "sample_id_flag": (_sample_log(id=True), "INSPECT_SAMPLE_ID_REQUIRED"),
    "sample_epoch_text": (_sample_log(epoch="1"), "INSPECT_EPOCH_REQUIRED"),
    "sample_epoch_missing": (_log(samples=[without(SAMPLE, "epoch")]), "INSPECT_EPOCH_REQUIRED"),
    "sample_scores_not_object": (_sample_log(scores=[]), "INSPECT_OBJECT_REQUIRED: sample scores"),
    "sample_score_without_value": (
        _sample_log(scores={"includes": {"answer": "x"}}),
        "INSPECT_SCORE_VALUE_REQUIRED: includes",
    ),
    "sample_score_not_object": (
        _sample_log(scores={"includes": "C"}),
        "INSPECT_SCORE_VALUE_REQUIRED: includes",
    ),
}


@pytest.mark.parametrize("fault", sorted(MALFORMED))
def test_malformed_or_drifted_shape_fails_closed(fault: str) -> None:
    payload, reason = MALFORMED[fault]
    with pytest.raises(InspectPayloadError) as caught:
        CODECS[(FORMAT, VERSION)](payload)
    assert caught.value.args == (reason,)
