"""Selected C55 binding refuses self-consistent but semantically changed fields."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.evidence.c55_selected import (
    C55SelectedError,
    validate_selected_c55_projection,
)

_FIXTURE = Path(__file__).resolve().parents[4] / "fixtures/conformance/goppi-c55-common"


def _documents() -> tuple[dict[str, Any], dict[str, Any], str]:
    assessment_bytes = (_FIXTURE / "assessment.json").read_bytes()
    assessment = cast(dict[str, Any], json.loads(assessment_bytes))
    result = cast(dict[str, Any], json.loads((_FIXTURE / "evidence-result.json").read_bytes()))
    return assessment, result, digest_bytes(assessment_bytes)


def test_pinned_c55_fixture_yields_only_ordered_case_ids_and_source_states() -> None:
    assessment, result, source_hash = _documents()
    case_ids, items = validate_selected_c55_projection(assessment, result, source_hash)

    assert case_ids == [f"synthetic-case-{index:02d}" for index in range(1, 8)]
    assert items == [
        {"availability": "observed", "value": True},
        {"availability": "observed", "value": False},
        {"availability": "abstained", "value": None},
        {"availability": "abstained", "value": None},
        {"availability": "missing", "value": None},
        {"availability": "error", "value": None},
        {"availability": "missing", "value": None},
    ]


@pytest.mark.parametrize(
    "reason",
    [
        "native_unavailable",
        "verifier_timeout",
        "verifier_exit_error",
        "verifier_protocol_error",
        "verifier_identity_mismatch",
        "verifier_output_limit",
        "verifier_process_failure",
        "cleanup_unresolved",
    ],
)
def test_all_accepted_operational_reasons_preserve_error_mapping(reason: str) -> None:
    assessment, result, source_hash = _documents()
    assessment["cases"][5]["reason_code"] = reason
    _, items = validate_selected_c55_projection(assessment, result, source_hash)
    assert items[5] == {"availability": "error", "value": None}


@pytest.mark.parametrize(
    "reason", ["not_started_after_control_mismatch", "not_started_after_operational_failure"]
)
def test_both_accepted_not_started_reasons_preserve_missing_mapping(reason: str) -> None:
    assessment, result, source_hash = _documents()
    assessment["cases"][6]["reason_code"] = reason
    _, items = validate_selected_c55_projection(assessment, result, source_hash)
    assert items[6] == {"availability": "missing", "value": None}


@pytest.mark.parametrize(
    ("index", "reason_code"),
    [
        (0, "binary_false"),
        (5, "control_outcome_mismatch"),
        (6, "not_provided"),
    ],
)
def test_disposition_reason_mismatch_is_rejected(index: int, reason_code: str) -> None:
    assessment, result, source_hash = _documents()
    assessment["cases"][index]["reason_code"] = reason_code
    with pytest.raises(C55SelectedError) as error:
        validate_selected_c55_projection(assessment, result, source_hash)
    assert error.value.reason == "C55_REASON_MISMATCH"
    assert error.value.location == f"/assessment/cases/{index}/reason_code"


@pytest.mark.parametrize(
    ("path", "changed", "reason"),
    [
        (("source", "format_version"), "v2", "C55_RESULT_SOURCE_MISMATCH"),
        (("source", "identity", "raw_hash"), "sha256:" + "0" * 64, "C55_RESULT_SOURCE_MISMATCH"),
        (("population", "observed_count"), 6, "C55_RESULT_POPULATION_MISMATCH"),
        (("processing", "state"), "not_analyzed", "C55_RESULT_PROCESSING_MISMATCH"),
        (("items", 0, "coordinate", "index"), 1, "C55_RESULT_ITEM_MISMATCH"),
        (("items", 0, "metric", "semantic_binding"), "bound", "C55_RESULT_ITEM_MISMATCH"),
        (("items", 0, "value"), 1, "C55_RESULT_ITEM_MISMATCH"),
        (("items", 2, "availability"), "missing", "C55_RESULT_ITEM_MISMATCH"),
        (("items", 5, "next_action"), "rerun_source", "C55_RESULT_ITEM_MISMATCH"),
        (("items", 6, "reason"), "SOURCE_ERROR", "C55_RESULT_ITEM_MISMATCH"),
        (("verification",), ["claimed"], "C55_RESULT_VERIFICATION_MISMATCH"),
    ],
)
def test_projection_or_source_binding_drift_is_rejected(
    path: tuple[str | int, ...], changed: object, reason: str
) -> None:
    assessment, result, source_hash = _documents()
    target: Any = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = changed
    with pytest.raises(C55SelectedError) as error:
        validate_selected_c55_projection(assessment, result, source_hash)
    assert error.value.reason == reason


def test_assessment_counts_and_order_are_selected_file_constraints() -> None:
    assessment, result, source_hash = _documents()
    changed = deepcopy(assessment)
    changed["counts"]["unknown"] = 0
    with pytest.raises(C55SelectedError) as error:
        validate_selected_c55_projection(changed, result, source_hash)
    assert error.value.reason == "C55_COUNTS_MISMATCH"

    changed = deepcopy(assessment)
    changed["cases"][1]["case_id"] = changed["cases"][0]["case_id"]
    with pytest.raises(C55SelectedError) as error:
        validate_selected_c55_projection(changed, result, source_hash)
    assert error.value.reason == "C55_CASE_ID_INVALID"


def test_bad_assessment_hash_and_unsupported_profile_are_rejected() -> None:
    assessment, result, source_hash = _documents()
    with pytest.raises(C55SelectedError) as error:
        validate_selected_c55_projection(assessment, result, "sha256:0")
    assert error.value.reason == "C55_ASSESSMENT_HASH_INVALID"
    assessment["schema"] = "different.v1"
    with pytest.raises(C55SelectedError) as error:
        validate_selected_c55_projection(assessment, result, source_hash)
    assert error.value.reason == "C55_PROFILE_UNSUPPORTED"
