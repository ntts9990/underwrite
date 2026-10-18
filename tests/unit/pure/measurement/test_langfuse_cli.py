"""Pinned CLI row meaning is separate from the server blob-export profile."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, cast

import pytest

from underwrite.instrument.evidence.langfuse_accounting import LangfuseAccountingError
from underwrite.instrument.evidence.langfuse_cli import (
    ASSOCIATIONS,
    VALUE_TYPES,
    account_langfuse_cli,
    index_langfuse_cli_body,
)


def observation(**changes: object) -> dict[str, object]:
    return {"id": "o", "projectId": "p", "traceId": "t", "type": "SPAN", **changes}


def score(**changes: object) -> dict[str, object]:
    return {
        "id": "s",
        "projectId": "p",
        "name": "quality",
        "dataType": "NUMERIC",
        "value": 0,
        "subject": {"kind": "observation", "id": "o", "traceId": "t"},
        **changes,
    }


def body(rows: list[dict[str, object]], meta: object | None = None) -> dict[str, object]:
    return {"data": rows, "meta": {} if meta is None else meta}


def envelope(inner: dict[str, object]) -> dict[str, object]:
    return {"status": 200, "headers": {"x-private": "SECRET_HEADER"}, "body": inner}


def run(observations: object | None = None, scores: object | None = None) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        account_langfuse_cli(
            body([observation()]) if observations is None else observations,
            body([score()], {"limit": 2}) if scores is None else scores,
        ),
    )


def test_typed_values_subject_kinds_and_index_withhold_private_content() -> None:
    scores = [
        score(id="n", dataType="NUMERIC", value=0),
        score(id="b", dataType="BOOLEAN", value=False),
        score(id="c", dataType="CATEGORICAL", value="café", subject={"kind": "trace", "id": "t"}),
        score(
            id="t", dataType="TEXT", value="private text", subject={"kind": "session", "id": "s"}
        ),
        score(id="r", dataType="CORRECTION", value="", subject={"kind": "experiment", "id": "e"}),
    ]
    result = run(
        envelope(body([observation(input="ignored private I/O")], {"cursor": "SECRET_CURSOR"})),
        body(scores, {"pages": 3, "fetchedItems": 5, "truncated": False}),
    )
    assert list(result["counts"]["by_data_type"].values()) == [1] * len(VALUE_TYPES)
    assert result["scores"][0]["value"] == 0
    assert result["scores"][1]["value"] is False
    assert result["scores"][2]["value"] == "café"
    assert result["scores"][4]["value"] == ""
    assert result["counts"]["associations"] == {
        "observation_local": 2,
        "unresolved_observation_reference": 0,
        "observation_context_unknown": 0,
        "other_subject": 3,
        "subject_unknown": 0,
    }
    assert result["retrieval"]["observations"]["pagination"]["cursor_state"] == "present"
    assert "FETCH_REMAINING_LANGFUSE_PAGES" in result["next_actions"]
    index = index_langfuse_cli_body(result)
    assert index["counts"] == result["counts"]
    assert cast(list[dict[str, Any]], index["score_rows"])[1]["data_type"] == "BOOLEAN"
    serialized = json.dumps(index)
    for private in (
        "SECRET_CURSOR",
        "SECRET_HEADER",
        "ignored private I/O",
        "private text",
        "café",
        "quality",
        '"id":',
    ):
        assert private not in serialized


def test_full_observation_key_allows_same_id_across_traces_and_refuses_repeat() -> None:
    observations = body([observation(traceId="t1"), observation(traceId="t2")])
    scores = body(
        [
            score(id="s1", subject={"kind": "observation", "id": "o", "traceId": "t1"}),
            score(id="s2", subject={"kind": "observation", "id": "o", "traceId": "t2"}),
            score(
                id="s3",
                projectId="other",
                subject={"kind": "observation", "id": "o", "traceId": "t1"},
            ),
            score(id="s4", subject={"kind": "observation", "id": "o", "traceId": None}),
        ],
        {"limit": 4},
    )
    result = run(observations, scores)
    assert [row["target_observation_index"] for row in result["scores"]] == [0, 1, None, None]
    assert result["counts"]["associations"]["unresolved_observation_reference"] == 1
    assert result["counts"]["associations"]["observation_context_unknown"] == 1
    assert [row["local_score_count"] for row in result["observations"]] == [1, 1]
    with pytest.raises(LangfuseAccountingError) as exc:
        run(body([observation(), observation()]), body([], {"limit": 1}))
    assert (exc.value.reason, exc.value.location) == (
        "LANGFUSE_CLI_AMBIGUOUS_OBSERVATION_KEY",
        "/observations/1/id",
    )
    with pytest.raises(LangfuseAccountingError, match="LANGFUSE_CLI_AMBIGUOUS_OBSERVATION_KEY"):
        run(body([observation(traceId=None), observation(traceId=None)]), body([], {"limit": 1}))


def test_missing_null_and_nonobservation_subjects_are_not_trace_scores() -> None:
    rows = [
        score(id="a", subject=None),
        {key: value for key, value in score(id="b").items() if key != "subject"},
        score(id="c", subject={"kind": "trace", "id": "t"}),
        score(id="d", subject={"kind": "observation", "id": "o"}),
    ]
    result = run(scores=body(rows, {"limit": 4}))
    assert [row["subject"]["availability"] for row in result["scores"]] == [
        "null",
        "absent",
        "present",
        "present",
    ]
    assert [row["association"] for row in result["scores"]] == [
        "subject_unknown",
        "subject_unknown",
        "other_subject",
        "observation_context_unknown",
    ]
    assert "INSPECT_LANGFUSE_SUBJECT_CONTEXT" in result["next_actions"]


def test_empty_and_truncated_capture_preserve_scope_without_success_verdict() -> None:
    result = run(body([], {}), body([], {"pages": 1, "fetchedItems": 0, "truncated": True}))
    assert result["counts"]["observations"] == result["counts"]["scores"] == 0
    assert result["counts"]["associations"] == dict.fromkeys(ASSOCIATIONS, 0)
    assert result["retrieval"]["scores"]["pagination"]["truncated"] is True
    assert result["scope"]["export_completeness"] == "unknown"
    assert result["planned"] == {
        "status": "unknown",
        "count": None,
        "missing_execution_count": None,
    }
    assert "FETCH_REMAINING_LANGFUSE_PAGES" in result["next_actions"]


def test_explicit_null_cursors_remain_distinct_from_absent_metadata_fields() -> None:
    result = run(body([], {"cursor": None}), body([], {"limit": 1, "cursor": None}))
    assert result["retrieval"]["observations"]["pagination"]["cursor_state"] == "null"
    assert result["retrieval"]["scores"]["pagination"]["cursor_state"] == "null"
    assert "FETCH_REMAINING_LANGFUSE_PAGES" not in result["next_actions"]
    assert result["scope"]["export_completeness"] == "unknown"


@pytest.mark.parametrize(
    "bad,role,reason",
    [
        (
            {"status": True, "headers": {}, "body": body([])},
            "observations",
            "LANGFUSE_CLI_NON_SUCCESS_STATUS",
        ),
        (
            {"status": 500, "headers": {}, "body": body([])},
            "observations",
            "LANGFUSE_CLI_NON_SUCCESS_STATUS",
        ),
        (
            {"status": 200, "headers": {"secret": 4}, "body": body([])},
            "observations",
            "LANGFUSE_CLI_HEADERS_INVALID",
        ),
        (
            {"status": 200, "headers": {}, "body": body([]), "data": []},
            "observations",
            "LANGFUSE_CLI_CONTAINER_INVALID",
        ),
        ({"data": [], "meta": None}, "observations", "LANGFUSE_CLI_OBJECT_REQUIRED"),
        ({"data": []}, "observations", "LANGFUSE_CLI_CONTAINER_INVALID"),
        (body([], {"cursor": "", "limit": 2}), "observations", "LANGFUSE_CLI_PAGINATION_INVALID"),
        (body([], {"limit": 1001}), "observations", "LANGFUSE_CLI_PAGINATION_INVALID"),
        (
            body([], {"pages": 101, "fetchedItems": 0, "truncated": False}),
            "observations",
            "LANGFUSE_CLI_PAGINATION_INVALID",
        ),
        (
            body([], {"pages": 1, "fetchedItems": 1, "truncated": False}),
            "observations",
            "LANGFUSE_CLI_FETCHED_COUNT_MISMATCH",
        ),
        (
            body([], {"pages": 1, "fetchedItems": False, "truncated": False}),
            "observations",
            "LANGFUSE_CLI_FETCHED_COUNT_MISMATCH",
        ),
        (
            body([], {"pages": 1, "fetchedItems": 0, "truncated": False, "cursor": "next"}),
            "observations",
            "LANGFUSE_CLI_PAGINATION_INVALID",
        ),
        (body([], {}), "scores", "LANGFUSE_CLI_FIELD_REQUIRED"),
        (body([], {"limit": 101}), "scores", "LANGFUSE_CLI_PAGINATION_INVALID"),
    ],
)
def test_container_and_pagination_refuse_finitely(bad: object, role: str, reason: str) -> None:
    with pytest.raises(LangfuseAccountingError) as exc:
        run(bad, body([], {"limit": 2})) if role == "observations" else run(body([]), bad)
    assert exc.value.reason == reason
    assert exc.value.location.startswith("/" + role)
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("role", ["observations", "scores"])
def test_one_page_never_contains_more_rows_than_its_reported_limit(role: str) -> None:
    observations = body([observation(), observation(id="second")], {"limit": 1})
    scores = body([score(), score(id="second")], {"limit": 1})
    with pytest.raises(LangfuseAccountingError) as exc:
        run(observations=observations) if role == "observations" else run(scores=scores)
    assert (exc.value.reason, exc.value.location) == (
        "LANGFUSE_CLI_PAGINATION_INVALID",
        f"/{role}/meta/limit",
    )


def test_cli_all_rows_cannot_exceed_reported_page_capacity() -> None:
    scores = body(
        [score(id=f"score-{index}") for index in range(101)],
        {"pages": 1, "fetchedItems": 101, "truncated": False},
    )
    with pytest.raises(LangfuseAccountingError) as exc:
        run(scores=scores)
    assert (exc.value.reason, exc.value.location) == (
        "LANGFUSE_CLI_PAGINATION_INVALID",
        "/scores/meta/pages",
    )


@pytest.mark.parametrize(
    "change,reason,location,code",
    [
        (
            {"dataType": "UNKNOWN"},
            "LANGFUSE_CLI_SCORE_KIND_UNSUPPORTED",
            "/scores/0/dataType",
            "UNSUPPORTED_PROFILE",
        ),
        (
            {"dataType": "BOOLEAN", "value": 0},
            "LANGFUSE_CLI_SCORE_VALUE_INVALID",
            "/scores/0/value",
            "INVALID_INPUT",
        ),
        (
            {"dataType": "NUMERIC", "value": False},
            "LANGFUSE_CLI_SCORE_VALUE_INVALID",
            "/scores/0/value",
            "INVALID_INPUT",
        ),
        (
            {"dataType": "TEXT", "value": "cafe\u0301 PRIVATE"},
            "LANGFUSE_CLI_NONCANONICAL_SCORE_VALUE",
            "/scores/0/value",
            "UNSUPPORTED_PROFILE",
        ),
        (
            {"subject": {"kind": "unsupported", "id": "o"}},
            "LANGFUSE_CLI_SUBJECT_KIND_UNSUPPORTED",
            "/scores/0/subject/kind",
            "UNSUPPORTED_PROFILE",
        ),
    ],
)
def test_invalid_selected_score_fields_are_bounded_and_do_not_echo_source(
    change: dict[str, object], reason: str, location: str, code: str
) -> None:
    with pytest.raises(LangfuseAccountingError) as exc:
        run(scores=body([score(**change)], {"limit": 2}))
    assert (exc.value.code, exc.value.reason, exc.value.location) == (code, reason, location)
    assert "PRIVATE" not in str(exc.value)


def test_duplicate_score_identity_is_separate_from_observation_scope() -> None:
    with pytest.raises(LangfuseAccountingError) as exc:
        run(scores=body([score(), score()], {"limit": 2}))
    assert (exc.value.reason, exc.value.location) == (
        "LANGFUSE_CLI_AMBIGUOUS_SCORE_KEY",
        "/scores/1/id",
    )
    result = run(scores=body([score(), score(projectId="other")], {"limit": 2}))
    assert result["counts"]["scores"] == len(result["scores"])


def test_ignored_optional_noncanonical_text_does_not_trigger_selected_value_rule() -> None:
    selected = score(dataType="TEXT", value="café", metadata="ignored cafe\u0301")
    original = deepcopy(selected)
    result = run(scores=body([selected], {"limit": 1}))
    assert result["scores"][0]["value"] == "café"
    assert selected == original
