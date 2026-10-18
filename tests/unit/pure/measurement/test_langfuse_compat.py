"""Auto Langfuse selection is a wire-family decision, not source-version inference."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from underwrite.instrument.evidence.langfuse_accounting import (
    MAX_OBSERVATIONS,
    MAX_SCORES,
    LangfuseAccountingError,
    account_langfuse,
)
from underwrite.instrument.evidence.langfuse_cli import account_langfuse_cli
from underwrite.instrument.evidence.langfuse_compat import (
    BLOB_READER,
    CLI_READER,
    account_langfuse_compatible,
    index_langfuse_compatible,
)

ROOT = Path("fixtures")
BLOB = ROOT / "golden/external/langfuse"
CLI = ROOT / "examples/langfuse-cli"


def _pair(family: str, *, body: bool = False) -> tuple[Any, Any]:
    if family == "blob":
        names = (
            BLOB / "observations_v2-2026-09-14T06-40-00.json",
            BLOB / "scores-2026-09-14T06-40-00.json",
        )
    else:
        names = (
            CLI / ("observations-body.json" if body else "observations.json"),
            CLI / ("scores-body.json" if body else "scores.json"),
        )
    return cast(tuple[Any, Any], tuple(json.loads(path.read_bytes()) for path in names))


def _account(observations: object, scores: object) -> dict[str, Any]:
    return cast(dict[str, Any], account_langfuse_compatible(observations, scores))


@pytest.mark.parametrize(("family", "reader"), [("blob", BLOB_READER), ("cli", CLI_READER)])
def test_auto_full_and_row_reuse_the_selected_pure_owner(family: str, reader: str) -> None:
    observations, scores = _pair(family)
    full = _account(observations, scores)
    owner = cast(
        dict[str, Any],
        account_langfuse(observations, scores)
        if family == "blob"
        else account_langfuse_cli(observations, scores),
    )
    row = cast(dict[str, Any], index_langfuse_compatible(full))
    assert full["reader_profile"] == row["reader_profile"] == reader
    for key in ("observations", "scores", "counts", "planned", "next_actions", "rule_profile"):
        assert full[key] == owner[key]
    if family == "cli":
        assert full["retrieval"] == row["retrieval"] == owner["retrieval"]
    assert len(cast(list[object], row["score_rows"])) == owner["counts"]["scores"]
    assert full["scope"]["selected_fields_only"] is True
    assert row["scope"]["values"] == "withheld"
    assert all(source["producer_version"] is None for source in full["sources"].values())
    assert "format_version" not in json.dumps(full["sources"])
    assert "synthetic review" not in json.dumps(row)


def test_cli_bodies_and_truncated_collection_keep_retrieval_distinctions() -> None:
    observations, scores = _pair("cli", body=True)
    body = _account(observations, scores)
    assert body["reader_profile"] == CLI_READER
    assert body["retrieval"]["observations"]["capture_kind"] == "output_body"
    assert body["retrieval"]["scores"]["capture_kind"] == "output_body"

    observations, _ = _pair("cli")
    truncated = json.loads((CLI / "scores-truncated.json").read_bytes())
    limited = _account(observations, truncated)
    assert limited["retrieval"]["scores"]["pagination"]["truncated"] is True
    assert "FETCH_REMAINING_LANGFUSE_PAGES" in limited["next_actions"]


def test_two_empty_arrays_choose_shape_without_claiming_blob_origin() -> None:
    result = _account([], [])
    assert result["reader_profile"] == BLOB_READER
    assert result["counts"]["scores"] == 0
    assert result["sources"]["scores"]["producer_version"] is None
    assert result["scope"]["source_windows"] == "unknown"


@pytest.mark.parametrize(
    "which", ["mixed", "bare_api_arrays", "conflicting_core", "unknown_object"]
)
def test_container_selection_refuses_without_a_semantic_fallback(which: str) -> None:
    cli_observations, cli_scores = _pair("cli")
    observations: Any
    scores: Any
    if which == "mixed":
        observations, scores = [], cli_scores
        reason = "LANGFUSE_AUTO_MIXED_FAMILIES"
    elif which == "bare_api_arrays":
        observations, scores = cli_observations["body"]["data"], cli_scores["body"]["data"]
        reason = "LANGFUSE_AUTO_FIELD_ALIAS"
    elif which == "conflicting_core":
        observations = {**cli_observations, "data": []}
        scores = cli_scores
        reason = "LANGFUSE_AUTO_CONTAINER_UNSUPPORTED"
    else:
        observations, scores = {"unrecognized": []}, cli_scores
        reason = "LANGFUSE_AUTO_CONTAINER_UNSUPPORTED"
    with pytest.raises(LangfuseAccountingError) as caught:
        account_langfuse_compatible(observations, scores)
    assert caught.value.code == "UNSUPPORTED_PROFILE"
    assert caught.value.reason == reason
    assert caught.value.location.startswith("/observations") or caught.value.location.startswith(
        "/scores"
    )


@pytest.mark.parametrize(
    ("role", "field"),
    [
        ("observations", "projectId"),
        ("observations", "traceId"),
        ("scores", "dataType"),
        ("scores", "subject"),
        ("scores", "stringValue"),
        ("scores", "longStringValue"),
    ],
)
def test_array_api_aliases_refuse_before_projection(role: str, field: str) -> None:
    observations, scores = _pair("blob")
    selected = observations if role == "observations" else scores
    selected[0][field] = "PRIVATE_CANARY"
    with pytest.raises(LangfuseAccountingError) as caught:
        account_langfuse_compatible(observations, scores)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "UNSUPPORTED_PROFILE",
        "LANGFUSE_AUTO_FIELD_ALIAS",
        f"/{role}/0",
    )
    assert "PRIVATE_CANARY" not in str(caught.value)


@pytest.mark.parametrize("field", ["session_id", "dataset_run_id"])
def test_array_target_conflict_uses_the_existing_association_owner(field: str) -> None:
    observations, scores = _pair("blob")
    scores[0][field] = "PRIVATE_CANARY"
    with pytest.raises(LangfuseAccountingError) as caught:
        account_langfuse_compatible(observations, scores)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "UNSUPPORTED_PROFILE",
        "LANGFUSE_SCORE_TARGET_CONFLICT",
        "/scores/0",
    )


@pytest.mark.parametrize("field", ["session_id", "dataset_run_id"])
def test_array_empty_other_target_column_keeps_local_association(field: str) -> None:
    observations, scores = _pair("blob")
    baseline = _account(observations, scores)
    scores[0][field] = ""
    selected = _account(observations, scores)
    assert selected["scores"][0]["association"] == baseline["scores"][0]["association"]
    assert (
        selected["scores"][0]["target_observation_index"]
        == baseline["scores"][0]["target_observation_index"]
    )


@pytest.mark.parametrize("field", ["stringValue", "longStringValue"])
def test_cli_older_text_value_alias_cannot_silently_override_typed_value(field: str) -> None:
    observations, scores = _pair("cli")
    for row in scores["body"]["data"]:
        if row["dataType"] == "TEXT":
            row[field] = "PRIVATE_CANARY"
            break
    with pytest.raises(LangfuseAccountingError) as caught:
        account_langfuse_compatible(observations, scores)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "UNSUPPORTED_PROFILE",
        "LANGFUSE_AUTO_FIELD_ALIAS",
        "/scores/3",
    )
    assert "PRIVATE_CANARY" not in str(caught.value)


@pytest.mark.parametrize("field", ["trace_id", "observationId", "longStringValue"])
def test_cli_top_level_identity_or_value_alias_refuses(field: str) -> None:
    observations, scores = _pair("cli")
    scores["body"]["data"][0][field] = "PRIVATE_CANARY"
    with pytest.raises(LangfuseAccountingError) as caught:
        account_langfuse_compatible(observations, scores)
    assert caught.value.location == "/scores/0"
    assert caught.value.reason == "LANGFUSE_AUTO_FIELD_ALIAS"


def test_cli_subject_alias_and_unexamined_additions_have_distinct_outcomes() -> None:
    observations, scores = _pair("cli")
    clean = _account(observations, scores)
    observations["body"]["data"][0]["privateExtra"] = "PRIVATE_CANARY"
    scores["body"]["data"][0]["privateExtra"] = "PRIVATE_CANARY"
    scores["privateEnvelopeExtra"] = "PRIVATE_CANARY"
    added = _account(observations, scores)
    assert added["counts"] == clean["counts"]
    assert "PRIVATE_CANARY" not in json.dumps(added)
    scores["body"]["data"][0]["subject"]["trace_id"] = "PRIVATE_CANARY"
    with pytest.raises(LangfuseAccountingError) as caught:
        account_langfuse_compatible(observations, scores)
    assert caught.value.location == "/scores/0/subject"


def test_pagination_extras_refuse_instead_of_falsely_reporting_no_next_page() -> None:
    observations, scores = _pair("cli", body=True)
    observations["meta"]["PRIVATE_CANARY"] = "has_more"
    with pytest.raises(LangfuseAccountingError) as caught:
        account_langfuse_compatible(observations, scores)
    assert (caught.value.reason, caught.value.location) == (
        "LANGFUSE_AUTO_PAGINATION_UNSUPPORTED",
        "/observations/meta",
    )


@pytest.mark.parametrize(
    ("role", "maximum"), [("observations", MAX_OBSERVATIONS), ("scores", MAX_SCORES)]
)
def test_collection_bounds_precede_projection(role: str, maximum: int) -> None:
    observations, scores = _pair("blob")
    row = deepcopy((observations if role == "observations" else scores)[0])
    expanded = [row for _ in range(maximum + 1)]
    if role == "observations":
        observations = expanded
    else:
        scores = expanded
    with pytest.raises(LangfuseAccountingError) as caught:
        account_langfuse_compatible(observations, scores)
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"
    assert caught.value.location == "/" + role


def test_unknown_reader_cannot_be_projected_to_a_row_contract() -> None:
    with pytest.raises(LangfuseAccountingError) as caught:
        index_langfuse_compatible({"reader_profile": "PRIVATE_CANARY"})
    assert caught.value.location == "/reader_profile"
