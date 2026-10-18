"""Score kinds and original-ID associations stay descriptive and bounded."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from underwrite_core.canonical import SAFE_INT_MAX

from underwrite.instrument.evidence.langfuse_accounting import (
    ASSOCIATION_RULES,
    KIND_RULES,
    MAX_ID_LENGTH,
    MAX_LABEL_LENGTH,
    MAX_OBSERVATIONS,
    MAX_SCORES,
    MAX_STRING_VALUE_LENGTH,
    LangfuseAccountingError,
    account_langfuse,
)


def observation(**changes: object) -> dict[str, Any]:
    return {
        "id": "observation",
        "project_id": "project",
        "trace_id": "trace",
        "type": "SPAN",
        **changes,
    }


def score(**changes: object) -> dict[str, Any]:
    return {
        "id": "score",
        "project_id": "project",
        "trace_id": "trace",
        "observation_id": "observation",
        "name": "rating",
        "data_type": "NUMERIC",
        "value": 1,
        "string_value": None,
        **changes,
    }


def run(observations: object, scores: object) -> dict[str, Any]:
    return account_langfuse(observations, scores)


def test_five_score_semantics_do_not_convert_text_or_category_zero_to_failure() -> None:
    observations = [
        observation(id="generation", type="GENERATION"),
        observation(id="retriever", type="RETRIEVER"),
        observation(id="root"),
    ]
    scores = [
        score(observation_id="generation"),
        score(observation_id="generation", data_type="BOOLEAN", value=1, string_value="True"),
        score(observation_id="retriever", data_type="CATEGORICAL", value=0, string_value="high"),
        score(observation_id=None, data_type="TEXT", value=0, string_value="note"),
        score(observation_id=None),
    ]
    result = run(observations, scores)
    assert result["counts"] == {
        "observations": 3,
        "scores": 5,
        "by_data_type": {"NUMERIC": 2, "BOOLEAN": 1, "CATEGORICAL": 1, "TEXT": 1},
        "associations": {
            "observation_local": 3,
            "trace_level": 2,
            "unresolved_observation_reference": 0,
        },
        "observations_without_local_scores": 1,
    }
    assert [row["local_score_count"] for row in result["observations"]] == [2, 1, 0]
    assert result["planned"] == {
        "status": "unknown",
        "count": None,
        "missing_execution_count": None,
    }
    assert result["scores"][2]["value"] == result["scores"][3]["value"] == 0
    assert "success" not in result["scores"][2]
    assert [row["score_index"] for row in result["scores"]] == list(range(len(scores)))


def test_full_original_key_case_and_whitespace_control_association() -> None:
    observations = [
        observation(),
        observation(project_id="other"),
        observation(trace_id="other"),
        observation(id="Observation"),
        observation(id=" observation "),
    ]
    scores = [
        score(),
        score(project_id="other"),
        score(trace_id="other"),
        score(observation_id="Observation"),
        score(observation_id=" observation "),
        score(project_id="missing"),
        score(observation_id=None),
    ]
    result = run(observations, scores)
    assert [row["target_observation_index"] for row in result["scores"]] == [
        0,
        1,
        2,
        3,
        4,
        None,
        None,
    ]
    assert result["scores"][-2]["association"] == "unresolved_observation_reference"
    assert result["scores"][-1]["association"] == "trace_level"
    assert "INSPECT_UNRESOLVED_OBSERVATION_REFERENCES" in result["next_actions"]


def test_duplicate_observation_keys_refuse_but_duplicate_score_ids_remain_occurrences() -> None:
    with pytest.raises(LangfuseAccountingError, match="LANGFUSE_AMBIGUOUS_OBSERVATION_KEY"):
        run([observation(), observation()], [])
    result = run([observation()], [score(), score()])
    assert [row["score_index"] for row in result["scores"]] == [0, 1]
    assert result["counts"]["scores"] == len(result["scores"])


@pytest.mark.parametrize("target", ["session_id", "dataset_run_id"])
def test_trace_and_other_primary_target_refuse_before_association(target: str) -> None:
    with pytest.raises(LangfuseAccountingError) as caught:
        run([observation()], [score(**{target: "other-target"})])
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "UNSUPPORTED_PROFILE",
        "LANGFUSE_SCORE_TARGET_CONFLICT",
        "/scores/0",
    )


@pytest.mark.parametrize(
    "targets",
    [{"session_id": ""}, {"dataset_run_id": ""}, {"session_id": "", "dataset_run_id": ""}],
)
def test_empty_other_target_columns_remain_absent_for_blob_association(
    targets: dict[str, str],
) -> None:
    result = run([observation()], [score(**targets)])
    assert result["scores"][0]["association"] == "observation_local"
    assert result["scores"][0]["target_observation_index"] == 0


@pytest.mark.parametrize("target", ["session_id", "dataset_run_id"])
@pytest.mark.parametrize("value", [False, 0, [], {}])
def test_nonstring_other_target_columns_refuse_without_python_truthiness(
    target: str, value: object
) -> None:
    with pytest.raises(LangfuseAccountingError) as caught:
        run([observation()], [score(**{target: value})])
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_INPUT",
        "LANGFUSE_SCORE_TARGET_INVALID",
        f"/scores/0/{target}",
    )


@pytest.mark.parametrize("target", ["session_id", "dataset_run_id"])
def test_null_trace_session_or_run_remains_outside_blob_reader(target: str) -> None:
    with pytest.raises(LangfuseAccountingError) as caught:
        run([], [score(trace_id=None, **{target: "only-target"})])
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_INPUT",
        "LANGFUSE_IDENTIFIER_INVALID",
        "/scores/0/trace_id",
    )


def test_display_text_normalizes_without_mutation_or_deduplication() -> None:
    observations = [observation(type="cafe\u0301")]
    scores = [
        score(name="cafe\u0301", string_value="A\u030a"),
        score(name="café", string_value=""),
        score(string_value=None),
    ]
    before = deepcopy((observations, scores))
    result = run(observations, scores)
    assert result["observations"][0]["type"] == "café"
    assert [row["name"] for row in result["scores"][:2]] == ["café", "café"]
    assert [row["string_value"] for row in result["scores"]] == ["Å", "", None]
    assert result["scope"]["display_text"] == "nfc_normalized"
    assert (observations, scores) == before


@pytest.mark.parametrize(
    "side,key",
    [
        ("observation", "id"),
        ("observation", "project_id"),
        ("observation", "trace_id"),
        ("score", "id"),
        ("score", "project_id"),
        ("score", "trace_id"),
        ("score", "observation_id"),
    ],
)
def test_opaque_identifier_nfd_is_refused_before_join(side: str, key: str) -> None:
    observations, scores = [observation()], [score()]
    (observations if side == "observation" else scores)[0][key] = "cafe\u0301"
    with pytest.raises(LangfuseAccountingError) as caught:
        run(observations, scores)
    assert caught.value.code == "UNSUPPORTED_PROFILE"
    assert caught.value.reason == "LANGFUSE_NONCANONICAL_IDENTIFIER"
    assert "cafe" not in caught.value.location


@pytest.mark.parametrize(
    "value", [False, None, "1", float("nan"), float("inf"), SAFE_INT_MAX + 1, 1e20]
)
def test_score_number_guards_never_coerce_boolean_or_null(value: object) -> None:
    with pytest.raises(LangfuseAccountingError, match="LANGFUSE_NUMBER_INVALID"):
        run([], [score(observation_id=None, value=value)])


@pytest.mark.parametrize("value", [-1, -0.25, 0, 1, 1e30])
def test_all_score_kinds_retain_arbitrary_supported_numeric_values(value: int | float) -> None:
    result = run(
        [], [score(observation_id=None, data_type=kind, value=value) for kind in KIND_RULES]
    )
    assert [row["value"] for row in result["scores"]] == [value] * len(KIND_RULES)
    for row in result["scores"]:
        assert row["kind_rule"] == KIND_RULES[row["data_type"]]
        assert row["association_rule"] == ASSOCIATION_RULES[row["association"]]


def test_missing_association_is_not_defaulted_to_trace_level() -> None:
    row = score()
    del row["observation_id"]
    with pytest.raises(LangfuseAccountingError, match="LANGFUSE_FIELD_REQUIRED"):
        run([], [row])


@pytest.mark.parametrize("value", ["", "   ", False, 0, [], "\ud800"])
def test_opaque_identifier_malformed_values_are_typed(value: object) -> None:
    with pytest.raises(LangfuseAccountingError, match="LANGFUSE_IDENTIFIER_INVALID"):
        run([], [score(id=value)])


def test_unknown_kind_is_not_inferred_from_value() -> None:
    with pytest.raises(LangfuseAccountingError) as caught:
        run([], [score(data_type="UNKNOWN", observation_id=None, value=1)])
    assert caught.value.code == "UNSUPPORTED_PROFILE"


def test_both_array_bounds_checked_before_any_poison_entry() -> None:
    for observations, scores in (
        ([object()] * (MAX_OBSERVATIONS + 1), []),
        ([object()], [object()] * (MAX_SCORES + 1)),
    ):
        with pytest.raises(LangfuseAccountingError) as caught:
            run(observations, scores)
        assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


def test_exact_population_limits_and_empty_populations_conserve_counts() -> None:
    observations = [observation(id=f"obs-{i}") for i in range(MAX_OBSERVATIONS)]
    scores = [score(observation_id=None) for _ in range(MAX_SCORES)]
    result = run(observations, scores)
    assert result["counts"]["observations"] == MAX_OBSERVATIONS
    assert result["counts"]["scores"] == MAX_SCORES
    assert sum(result["counts"]["by_data_type"].values()) == MAX_SCORES
    assert sum(result["counts"]["associations"].values()) == MAX_SCORES
    assert run([], [])["counts"]["observations_without_local_scores"] == 0


@pytest.mark.parametrize(
    "key,maximum",
    [("id", MAX_ID_LENGTH), ("name", MAX_LABEL_LENGTH), ("string_value", MAX_STRING_VALUE_LENGTH)],
)
def test_text_caps_never_truncate(key: str, maximum: int) -> None:
    run([], [score(**{key: "x" * maximum})])
    with pytest.raises(LangfuseAccountingError) as caught:
        run([], [score(**{key: "x" * (maximum + 1)})])
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


def test_normalization_expansion_obeys_output_text_bound() -> None:
    # U+0344 decomposes into two combining code points even under NFC.
    with pytest.raises(LangfuseAccountingError) as caught:
        run([], [score(name="\u0344" * MAX_LABEL_LENGTH)])
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    "observations,scores", [(None, []), ([], None), ([None], []), ([], [None])]
)
def test_wrong_container_types_are_typed(observations: object, scores: object) -> None:
    with pytest.raises(LangfuseAccountingError) as caught:
        run(observations, scores)
    assert caught.value.code == "INVALID_INPUT"


@pytest.mark.parametrize(
    "value,expected_type", [(1.0, int), (-0.0, int), (0.25, float), (1e30, float)]
)
def test_numeric_api_representation_matches_canonical_json_without_source_mutation(
    value: float,
    expected_type: type[int] | type[float],
) -> None:
    import json
    import math

    from underwrite_core.canonical import canonical_bytes

    original = score(value=value)
    result = run([observation()], [original])
    normalized = result["scores"][0]["value"]
    decoded = json.loads(canonical_bytes(result))["scores"][0]["value"]
    assert type(normalized) is type(decoded) is expected_type
    assert normalized == decoded == value
    assert type(original["value"]) is float
    assert math.copysign(1, original["value"]) == math.copysign(1, value)
