"""Declared source-claim relationships retain missingness and row identity."""

from __future__ import annotations

from typing import cast

import pytest

from underwrite.instrument.evidence.cross_artifact_relation import (
    CASE_ID_MAX_LENGTH,
    RelationError,
    inspect_promptfoo_source_case_metadata,
    review_declared_cases,
)


def test_source_metadata_inspector_reports_both_missing_owner_fields_without_values() -> None:
    source: dict[str, object] = {"testCase": {"metadata": {}}, "prompt": "PRIVATE_CASE_CANARY"}
    index = 3
    inspected = inspect_promptfoo_source_case_metadata(source, index)
    assert inspected["case_id"] is None and inspected["slot_kind"] is None
    assert [item["code"] for item in inspected["blockers"]] == [
        "PROMPTFOO_CASE_ID_REQUIRED",
        "PROMPTFOO_SLOT_KIND_REQUIRED",
    ]
    assert all(item["row_index"] == index for item in inspected["blockers"])
    assert "PRIVATE_CASE_CANARY" not in str(inspected)


def test_source_metadata_inspector_keeps_valid_role_without_oversized_case_id() -> None:
    source: dict[str, object] = {
        "testCase": {
            "metadata": {"case_id": "S" * (CASE_ID_MAX_LENGTH + 1), "slot_kind": "subject"}
        }
    }
    inspected = inspect_promptfoo_source_case_metadata(source, 0)
    assert inspected["case_id"] is None
    assert inspected["slot_kind"] == "subject"
    assert [item["code"] for item in inspected["blockers"]] == [
        "PROMPTFOO_CASE_ID_REQUIRED"
    ]


def _items() -> list[dict[str, object]]:
    return [
        {"availability": "observed", "value": True},
        {"availability": "observed", "value": False},
        {"availability": "missing", "value": None},
    ]


def _rows() -> list[dict[str, object]]:
    return [
        {
            "row_index": 0,
            "case_id": "case-a",
            "slot_kind": "control",
            "source_success": False,
            "category": "failures",
        },
        {
            "row_index": 1,
            "case_id": "case-a",
            "slot_kind": "subject",
            "source_success": True,
            "category": "successes",
        },
        {
            "row_index": 2,
            "case_id": "case-b",
            "slot_kind": "subject",
            "source_success": True,
            "category": "successes",
        },
        {
            "row_index": 3,
            "case_id": "case-c",
            "slot_kind": "subject",
            "source_success": False,
            "category": "failures",
        },
    ]


def _pairs() -> list[dict[str, object]]:
    return [
        {"case_index": 0, "row_index": 1, "case_id": "case-a"},
        {"case_index": 1, "row_index": 2, "case_id": "case-b"},
        {"case_index": 2, "row_index": 3, "case_id": "case-c"},
    ]


def _review(
    *,
    items: object | None = None,
    rows: object | None = None,
    pairs: object | None = None,
    coverage: object = "complete",
) -> dict[str, object]:
    return review_declared_cases(
        _items() if items is None else items,
        ["case-a", "case-b", "case-c"],
        _rows() if rows is None else rows,
        _pairs() if pairs is None else pairs,
        coverage,
    )


def test_complete_relation_compares_only_available_source_booleans() -> None:
    result = _review()

    assert result["pairs"] == [
        {
            "case_index": 0,
            "row_index": 1,
            "goppi_availability": "observed",
            "goppi_value": True,
            "promptfoo_source_success": True,
            "promptfoo_category": "successes",
            "finding": "source_boolean_values_equal",
        },
        {
            "case_index": 1,
            "row_index": 2,
            "goppi_availability": "observed",
            "goppi_value": False,
            "promptfoo_source_success": True,
            "promptfoo_category": "successes",
            "finding": "source_boolean_values_different",
        },
        {
            "case_index": 2,
            "row_index": 3,
            "goppi_availability": "missing",
            "goppi_value": None,
            "promptfoo_source_success": False,
            "promptfoo_category": "failures",
            "finding": "value_comparison_ineligible",
        },
    ]
    assert result["finding_counts"] == {"equal": 1, "different": 1, "ineligible": 1}
    assert result["unmatched_case_indices"] == []
    assert result["unmatched_subject_row_indices"] == []
    assert result["next_verification_tasks"] == []
    assert "case-a" not in str(result)


def test_nonempty_whitespace_case_id_is_compared_exactly_without_echo() -> None:
    rows = _rows()
    rows[1]["case_id"] = " "
    pairs = _pairs()
    pairs[0]["case_id"] = " "

    result = review_declared_cases(
        _items(), [" ", "case-b", "case-c"], rows, pairs, "complete"
    )

    assert result["finding_counts"] == {"equal": 1, "different": 1, "ineligible": 1}
    assert "case-b" not in str(result)


def test_partial_relation_reports_unmatched_coordinates() -> None:
    result = _review(pairs=_pairs()[:1], coverage="partial")

    assert result["unmatched_case_indices"] == [1, 2]
    assert result["unmatched_subject_row_indices"] == [2, 3]
    assert result["finding_counts"] == {"equal": 1, "different": 0, "ineligible": 0}
    assert result["next_verification_tasks"] == ["DECLARE_REMAINING_CASE_CORRESPONDENCE"]


@pytest.mark.parametrize(
    ("changed_pairs", "reason"),
    [
        ([{"case_index": 0, "row_index": 0, "case_id": "case-a"}], "RELATION_CONTROL_MAPPED"),
        ([{"case_index": 0, "row_index": 1, "case_id": "case-b"}], "RELATION_CASE_ID_MISMATCH"),
        ([{"case_index": 0, "row_index": 2, "case_id": "case-a"}], "RELATION_CASE_ID_MISMATCH"),
        (
            [{"case_index": 3, "row_index": 1, "case_id": "case-a"}],
            "RELATION_CASE_INDEX_OUT_OF_RANGE",
        ),
        (
            [{"case_index": 0, "row_index": 4, "case_id": "case-a"}],
            "RELATION_ROW_INDEX_OUT_OF_RANGE",
        ),
        (_pairs()[:1] + [_pairs()[0]], "RELATION_CASE_INDEX_DUPLICATE"),
        (
            [_pairs()[0], {"case_index": 1, "row_index": 1, "case_id": "case-a"}],
            "RELATION_ROW_INDEX_DUPLICATE",
        ),
    ],
)
def test_invalid_mapping_is_rejected(changed_pairs: object, reason: str) -> None:
    with pytest.raises(RelationError) as error:
        _review(pairs=changed_pairs, coverage="partial")
    assert error.value.reason == reason
    assert error.value.location.startswith("/pairs/")


def test_complete_requires_all_cases_and_subjects() -> None:
    with pytest.raises(RelationError) as error:
        _review(pairs=_pairs()[:2])
    assert error.value.reason == "RELATION_COMPLETE_COVERAGE_MISSING"
    assert error.value.location == "/coverage"


def test_complete_requires_every_subject_even_when_all_goppi_cases_are_mapped() -> None:
    rows = _rows()
    rows.append(
        {
            "row_index": 4,
            "case_id": "extra",
            "slot_kind": "subject",
            "source_success": False,
            "category": "failures",
        }
    )
    with pytest.raises(RelationError) as error:
        _review(rows=rows)
    assert error.value.reason == "RELATION_COMPLETE_COVERAGE_MISSING"


@pytest.mark.parametrize(
    ("items", "reason"),
    [
        ([{"availability": "observed", "value": 0}], "RELATION_GOPPI_VALUE_INVALID"),
        ([{"availability": "observed", "value": None}], "RELATION_GOPPI_VALUE_INVALID"),
        ([{"availability": "missing", "value": False}], "RELATION_GOPPI_VALUE_INVALID"),
        ([{"availability": "abstained", "value": True}], "RELATION_GOPPI_VALUE_INVALID"),
        ([{"availability": "error", "value": False}], "RELATION_GOPPI_VALUE_INVALID"),
    ],
)
def test_goppi_availability_and_value_are_distinct(
    items: list[dict[str, object]], reason: str
) -> None:
    changed = _items()
    changed[0] = items[0]
    with pytest.raises(RelationError) as error:
        _review(items=changed, pairs=[], coverage="partial")
    assert error.value.reason == reason


def test_promptfoo_numeric_zero_is_not_boolean_false() -> None:
    rows = _rows()
    rows[3]["source_success"] = 0
    with pytest.raises(RelationError) as error:
        _review(rows=rows)
    assert error.value.reason == "RELATION_PROMPTFOO_SUCCESS_INVALID"
    assert error.value.location == "/promptfoo_rows/3/source_success"


def test_promptfoo_error_is_ineligible_even_when_false_values_match() -> None:
    rows = _rows()
    rows[2].update(source_success=False, category="errors")
    result = _review(rows=rows)
    pair = cast(list[dict[str, object]], result["pairs"])[1]
    assert pair["goppi_value"] is False
    assert pair["promptfoo_source_success"] is False
    assert pair["promptfoo_category"] == "errors"
    assert pair["finding"] == "value_comparison_ineligible"
    assert result["finding_counts"] == {"equal": 1, "different": 0, "ineligible": 2}


def test_promptfoo_assertion_failure_remains_eligible_for_boolean_comparison() -> None:
    rows = _rows()
    rows[2].update(source_success=False, category="failures")
    result = _review(rows=rows)
    assert cast(list[dict[str, object]], result["pairs"])[1]["finding"] == (
        "source_boolean_values_equal"
    )


@pytest.mark.parametrize("category", [None, 0, "failed"])
def test_invalid_promptfoo_category_is_rejected(category: object) -> None:
    rows = _rows()
    rows[2]["category"] = category
    with pytest.raises(RelationError) as error:
        _review(rows=rows)
    assert error.value.reason == "RELATION_PROMPTFOO_CATEGORY_INVALID"
    assert error.value.location == "/promptfoo_rows/2/category"


def test_promptfoo_category_and_success_must_agree() -> None:
    rows = _rows()
    rows[2]["category"] = "errors"
    with pytest.raises(RelationError) as error:
        _review(rows=rows)
    assert error.value.reason == "RELATION_PROMPTFOO_CATEGORY_MISMATCH"
    assert error.value.location == "/promptfoo_rows/2/category"


def test_duplicate_promptfoo_row_indices_are_rejected() -> None:
    rows = _rows()
    rows[3]["row_index"] = 2
    with pytest.raises(RelationError) as error:
        _review(rows=rows)
    assert error.value.reason == "RELATION_PROMPTFOO_ROW_INDEX_INVALID"
    assert error.value.location == "/promptfoo_rows/3/row_index"
