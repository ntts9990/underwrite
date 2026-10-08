"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from _external_formats import ingestor, receive, without
from _repo_paths import repo_root
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.ingest.codecs.langfuse import (
    CODECS,
    LangfusePayloadError,
    read_observations_v2,
    read_scores,
)
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor, UnsupportedFormat

ROOT = repo_root(Path(__file__))
GOLDEN = ROOT / "fixtures/golden/external/langfuse"
OBSERVATIONS = GOLDEN / "observations_v2-2026-09-14T06-40-00.json"
SCORES = GOLDEN / "scores-2026-09-14T06-40-00.json"
VERSION = "4.35.0"
PROFILES = {
    OBSERVATIONS: ("langfuse.observations-v2", "observations"),
    SCORES: ("langfuse.scores", "scores"),
}
PAYLOAD_KEYS = dict(PROFILES.values())  # format name -> the one key the codec preserves rows under


def _ingestor() -> Ingestor:
    return ingestor(CODECS, max_bytes=65536, max_depth=32)


def _receive(payload: object, format_name: str) -> dict[str, object]:
    source = Source(format_name, VERSION, "urn:test:langfuse")
    return receive(_ingestor(), payload, source, "in-memory:test")


def _real_rows(path: Path) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], json.loads(path.read_bytes()))


# Read each capture once: the malformed cases below are all real rows with one field changed.
OBSERVATION_ROWS = _real_rows(OBSERVATIONS)
SCORE_ROWS = _real_rows(SCORES)


@pytest.mark.parametrize("data_type", [[], {}, ["NUMERIC"]])
def test_unhashable_score_type_has_typed_error(data_type: object) -> None:
    rows = [{**SCORE_ROWS[0], "data_type": data_type}]
    with pytest.raises(LangfusePayloadError, match="LANGFUSE_SCORE_DATA_TYPE_UNSUPPORTED"):
        _receive(rows, "langfuse.scores")


def test_registry_pins_exactly_the_two_profiles() -> None:
    assert dict(CODECS) == {
        ("langfuse.observations-v2", VERSION): read_observations_v2,
        ("langfuse.scores", VERSION): read_scores,
    }


def test_fixture_record_binds_profiles_and_manifest_bytes() -> None:
    record = json.loads((GOLDEN / "observations.json").read_text(encoding="utf-8"))
    for path, (format_name, _) in PROFILES.items():
        entry = record["files"][path.name]
        assert entry["source"] == {"format": format_name, "format_version": VERSION}
    for name, item in record["run_manifest"].items():
        assert digest_bytes((GOLDEN / name).read_bytes()) == "sha256:" + item["sha256"]

    assert record["tool"]["server_version"] == VERSION


@pytest.mark.parametrize("path", list(PROFILES), ids=lambda p: p.name.split("-")[0])
def test_real_capture_payload_is_the_whole_document_under_its_one_key(path: Path) -> None:
    # File/body identity, kind, source and raw hash are the matrix's claim for every
    # manifest entry; this reader states only how its codec wraps the whole document.
    format_name, key = PROFILES[path]
    raw = path.read_bytes()
    source = Source(format_name, VERSION, "urn:test:langfuse")
    observation = _ingestor().file(path, source).observation
    assert observation["payload"] == {key: json.loads(raw)}


def test_real_capture_keeps_every_score_data_type_as_written() -> None:
    types = {row["data_type"] for row in SCORE_ROWS}
    assert types == {"NUMERIC", "BOOLEAN", "CATEGORICAL", "TEXT"}
    payload = _receive(SCORE_ROWS, "langfuse.scores")["payload"]
    scores = cast(list[dict[str, object]], cast(dict[str, object], payload)["scores"])
    by_name = {row["name"]: row for row in scores}
    assert by_name["relevance"]["value"] == 0 and by_name["relevance"]["string_value"] == "high"
    assert by_name["exact_match"]["value"] == 1 and by_name["exact_match"]["string_value"] is None


def test_real_capture_keeps_observation_tree_and_korean_text_intact() -> None:
    by_name = {row["name"]: row for row in OBSERVATION_ROWS}
    root = by_name["underwrite-capture-root"]
    # ClickHouse writes the absent parent as "" (not null); the reader keeps it that way.
    assert root["is_root_observation"] is True and root["parent_observation_id"] == ""
    assert by_name["answer"]["parent_observation_id"] == root["id"]
    assert by_name["answer"]["type"] == "GENERATION"
    assert "한글" in json.dumps(root["input"], ensure_ascii=False)
    payload = _receive(OBSERVATION_ROWS, "langfuse.observations-v2")["payload"]
    assert cast(dict[str, object], payload)["observations"] == OBSERVATION_ROWS


@pytest.mark.parametrize(
    "selector",
    [
        ("langfuse.observations-v2", "4.7.0"),
        ("langfuse.scores", "unknown"),
        ("langfuse.observations-v2", "latest"),
        ("langfuse", VERSION),
        ("langfuse.traces", VERSION),
    ],
)
def test_unpinned_selector_is_unsupported(selector: tuple[str, str]) -> None:
    with pytest.raises(UnsupportedFormat, match="^UNSUPPORTED_FORMAT$"):
        _ingestor().file(OBSERVATIONS, Source(selector[0], selector[1], "urn:test"))


@pytest.mark.parametrize("format_name", sorted(PAYLOAD_KEYS))
def test_empty_export_is_a_file_with_no_rows(format_name: str) -> None:
    assert _receive([], format_name)["payload"] == {PAYLOAD_KEYS[format_name]: []}


@pytest.mark.parametrize("value", [0.87, -1.5, 7])
def test_numeric_score_value_is_kept_as_written(value: float) -> None:
    payload = _receive([_score(value=value)], "langfuse.scores")["payload"]
    scores = cast(list[dict[str, object]], cast(dict[str, object], payload)["scores"])
    assert scores[0]["value"] == value and type(scores[0]["value"]) is type(value)


def test_written_null_string_value_is_the_exports_own_no_text() -> None:
    payload = _receive([_score(string_value=None)], "langfuse.scores")["payload"]
    scores = cast(list[dict[str, object]], cast(dict[str, object], payload)["scores"])
    assert scores[0]["string_value"] is None


def _observation(**overrides: object) -> dict[str, object]:
    return {**OBSERVATION_ROWS[0], **overrides}


def _score(**overrides: object) -> dict[str, object]:
    return {**SCORE_ROWS[0], **overrides}


MALFORMED: dict[str, tuple[str, object, str]] = {
    "observations_root_not_array": (
        "langfuse.observations-v2",
        {},
        "LANGFUSE_ARRAY_REQUIRED: observations",
    ),
    "scores_root_not_array": ("langfuse.scores", {}, "LANGFUSE_ARRAY_REQUIRED: scores"),
    "observation_not_object": (
        "langfuse.observations-v2",
        ["x"],
        "LANGFUSE_OBJECT_REQUIRED: observation",
    ),
    "observation_unknown_field": (
        "langfuse.observations-v2",
        [_observation(mixpanel_session_id="x")],
        "LANGFUSE_UNKNOWN_FIELD: observation",
    ),
    "observation_id_missing": (
        "langfuse.observations-v2",
        [without(_observation(), "id")],
        "LANGFUSE_MISSING_FIELD: observation",
    ),
    "observation_trace_blank": (
        "langfuse.observations-v2",
        [_observation(trace_id=" ")],
        "LANGFUSE_IDENTIFIER_REQUIRED: trace_id",
    ),
    "observation_type_not_text": (
        "langfuse.observations-v2",
        [_observation(type=1)],
        "LANGFUSE_IDENTIFIER_REQUIRED: type",
    ),
    "observation_start_time_null": (
        "langfuse.observations-v2",
        [_observation(start_time=None)],
        "LANGFUSE_IDENTIFIER_REQUIRED: start_time",
    ),
    "score_not_object": ("langfuse.scores", [1], "LANGFUSE_OBJECT_REQUIRED: score"),
    "score_unknown_field": (
        "langfuse.scores",
        [_score(config_id="x")],
        "LANGFUSE_UNKNOWN_FIELD: score",
    ),
    "score_name_missing": (
        "langfuse.scores",
        [without(_score(), "name")],
        "LANGFUSE_MISSING_FIELD: score",
    ),
    "score_project_id_blank": (
        "langfuse.scores",
        [_score(project_id=" ")],
        "LANGFUSE_IDENTIFIER_REQUIRED: project_id",
    ),
    "score_id_null": (
        "langfuse.scores",
        [_score(id=None)],
        "LANGFUSE_IDENTIFIER_REQUIRED: id",
    ),
    "score_timestamp_blank": (
        "langfuse.scores",
        [_score(timestamp="")],
        "LANGFUSE_IDENTIFIER_REQUIRED: timestamp",
    ),
    "score_data_type_unknown": (
        "langfuse.scores",
        [_score(data_type="RANKING")],
        "LANGFUSE_SCORE_DATA_TYPE_UNSUPPORTED",
    ),
    "score_data_type_missing": (
        "langfuse.scores",
        [without(_score(), "data_type")],
        "LANGFUSE_MISSING_FIELD: score",
    ),
    "score_value_missing": (
        "langfuse.scores",
        [without(_score(), "value")],
        "LANGFUSE_MISSING_FIELD: score",
    ),
    "score_value_flag": (
        "langfuse.scores",
        [_score(value=True)],
        "LANGFUSE_NUMBER_REQUIRED: value",
    ),
    "score_value_text": (
        "langfuse.scores",
        [_score(value="1")],
        "LANGFUSE_NUMBER_REQUIRED: value",
    ),
    "score_string_value_missing": (
        "langfuse.scores",
        [without(_score(), "string_value")],
        "LANGFUSE_MISSING_FIELD: score",
    ),
    "score_string_value_number": (
        "langfuse.scores",
        [_score(string_value=1)],
        "LANGFUSE_TEXT_OR_NULL_REQUIRED: string_value",
    ),
}


@pytest.mark.parametrize("fault", sorted(MALFORMED))
def test_malformed_or_drifted_shape_fails_closed(fault: str) -> None:
    format_name, payload, reason = MALFORMED[fault]
    with pytest.raises(LangfusePayloadError) as caught:
        CODECS[(format_name, VERSION)](payload)
    assert caught.value.args == (reason,)
