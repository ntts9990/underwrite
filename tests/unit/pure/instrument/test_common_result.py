"""Cross-item invariants for analyzed common results."""

from __future__ import annotations

import pytest

from underwrite.instrument.evidence.common_result import (
    ResultInvariantError,
    check_result_invariants,
)


def _result() -> dict[str, object]:
    return {
        "processing": {"state": "analyzed"},
        "population": {"observed_count": 2},
        "items": [
            {"coordinate": {"kind": "case", "index": 0}},
            {"coordinate": {"kind": "case", "index": 3}},
        ],
    }


def test_unique_sparse_coordinates_are_valid() -> None:
    check_result_invariants(_result())


def test_duplicate_coordinate_is_a_finite_error() -> None:
    result = _result()
    items = result["items"]
    assert isinstance(items, list)
    items[1] = {"coordinate": {"kind": "case", "index": 0}}
    with pytest.raises(ResultInvariantError) as caught:
        check_result_invariants(result)
    assert (caught.value.reason, caught.value.location) == (
        "RESULT_COORDINATE_DUPLICATE", "/items/1/coordinate"
    )


def test_omitted_item_disagrees_with_parsed_record_count() -> None:
    result = _result()
    result["items"] = [{"coordinate": {"kind": "case", "index": 0}}]
    with pytest.raises(ResultInvariantError) as caught:
        check_result_invariants(result)
    assert (caught.value.reason, caught.value.location) == ("RESULT_ITEM_COUNT_MISMATCH", "/items")


def test_distinct_metric_occurrences_and_kinds_do_not_collide() -> None:
    result = _result()
    result["population"] = {"observed_count": 3}
    result["items"] = [
        {"coordinate": {"kind": "case", "index": 0}},
        {"coordinate": {"kind": "row", "index": 0}},
        {"coordinate": {"kind": "metric_occurrence", "case_index": 0, "metric_index": 0}},
    ]
    check_result_invariants(result)


def test_malformed_coordinate_has_finite_diagnostic() -> None:
    result = _result()
    result["items"] = [
        {"coordinate": {"kind": "case", "index": 0}},
        {"coordinate": {"kind": "case", "index": "PRIVATE_VALUE"}},
    ]
    with pytest.raises(ResultInvariantError) as caught:
        check_result_invariants(result)
    assert (caught.value.reason, caught.value.location) == (
        "RESULT_COORDINATE_INVALID", "/items/1/coordinate"
    )
    assert "PRIVATE_VALUE" not in str(caught.value)
