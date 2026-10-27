"""Pure relationship between declared C55 cases and Promptfoo subject rows.

The result describes source Boolean fields and missingness only. It makes no
claim that inputs, metrics, or producer execution are equivalent or authentic.
"""

from __future__ import annotations

from typing import cast

_AVAILABILITIES = frozenset({"observed", "missing", "abstained", "error"})
_PROMPTFOO_CATEGORIES = frozenset({"successes", "failures", "errors"})
_NEXT_TASK = "DECLARE_REMAINING_CASE_CORRESPONDENCE"
_FINDING_KEYS = {
    "source_boolean_values_equal": "equal",
    "source_boolean_values_different": "different",
    "value_comparison_ineligible": "ineligible",
}


class RelationError(ValueError):
    """Finite input refusal with a structural location and no source text."""

    def __init__(self, reason: str, location: str) -> None:
        self.reason, self.location = reason, location
        super().__init__(reason)


def _list(value: object, reason: str, location: str) -> list[object]:
    if type(value) is not list:
        raise RelationError(reason, location)
    return cast(list[object], value)


def _object(value: object, reason: str, location: str) -> dict[str, object]:
    if type(value) is not dict:
        raise RelationError(reason, location)
    mapping = cast(dict[object, object], value)
    if any(type(key) is not str for key in mapping):
        raise RelationError(reason, location)
    return cast(dict[str, object], mapping)


def _index(value: object, length: int, reason: str, location: str) -> int:
    if type(value) is not int or value < 0 or value >= length:
        raise RelationError(reason, location)
    return value


def _goppi_items(items: object, case_ids: object) -> list[tuple[str, dict[str, object]]]:
    selected = _list(items, "RELATION_GOPPI_ITEMS_INVALID", "/goppi_items")
    ids = _list(case_ids, "RELATION_GOPPI_CASE_IDS_INVALID", "/goppi_case_ids")
    if len(selected) != len(ids):
        raise RelationError("RELATION_GOPPI_CASE_COUNT_MISMATCH", "/goppi_case_ids")
    validated_ids: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(ids):
        location = f"/goppi_case_ids/{index}"
        if type(value) is not str or not value or value in seen:
            raise RelationError("RELATION_GOPPI_CASE_ID_INVALID", location)
        seen.add(value)
        validated_ids.append(value)
    cases: list[tuple[str, dict[str, object]]] = []
    for index, value in enumerate(selected):
        location = f"/goppi_items/{index}"
        item = _object(value, "RELATION_GOPPI_ITEM_INVALID", location)
        availability = item.get("availability")
        if type(availability) is not str or availability not in _AVAILABILITIES:
            raise RelationError("RELATION_GOPPI_AVAILABILITY_INVALID", f"{location}/availability")
        observed = availability == "observed"
        field = item.get("value")
        if (observed and type(field) is not bool) or (not observed and field is not None):
            raise RelationError("RELATION_GOPPI_VALUE_INVALID", f"{location}/value")
        cases.append((validated_ids[index], item))
    return cases


def _promptfoo_rows(rows: object) -> tuple[dict[int, dict[str, object]], set[int]]:
    selected = _list(rows, "RELATION_PROMPTFOO_ROWS_INVALID", "/promptfoo_rows")
    indexed: dict[int, dict[str, object]] = {}
    subjects: set[int] = set()
    for index, value in enumerate(selected):
        location = f"/promptfoo_rows/{index}"
        row = _object(value, "RELATION_PROMPTFOO_ROW_INVALID", location)
        # The shell passes the complete row list in original source order.
        if type(row.get("row_index")) is not int or row["row_index"] != index:
            raise RelationError("RELATION_PROMPTFOO_ROW_INDEX_INVALID", f"{location}/row_index")
        case_id = row.get("case_id")
        if type(case_id) is not str or not case_id:
            raise RelationError("RELATION_PROMPTFOO_CASE_ID_INVALID", f"{location}/case_id")
        if row.get("slot_kind") not in ("subject", "control"):
            raise RelationError("RELATION_PROMPTFOO_SLOT_KIND_INVALID", f"{location}/slot_kind")
        if type(row.get("source_success")) is not bool:
            raise RelationError("RELATION_PROMPTFOO_SUCCESS_INVALID", f"{location}/source_success")
        category = row.get("category")
        if type(category) is not str or category not in _PROMPTFOO_CATEGORIES:
            raise RelationError("RELATION_PROMPTFOO_CATEGORY_INVALID", f"{location}/category")
        if (category == "successes") is not row["source_success"]:
            raise RelationError("RELATION_PROMPTFOO_CATEGORY_MISMATCH", f"{location}/category")
        indexed[index] = row
        if row["slot_kind"] == "subject":
            subjects.add(index)
    return indexed, subjects


def _pair(
    value: object,
    index: int,
    cases: list[tuple[str, dict[str, object]]],
    rows: dict[int, dict[str, object]],
    seen_cases: set[int],
    seen_rows: set[int],
) -> dict[str, object]:
    location = f"/pairs/{index}"
    pair = _object(value, "RELATION_PAIR_INVALID", location)
    case_index = _index(
        pair.get("case_index"),
        len(cases),
        "RELATION_CASE_INDEX_OUT_OF_RANGE",
        f"{location}/case_index",
    )
    row_index = _index(
        pair.get("row_index"), len(rows), "RELATION_ROW_INDEX_OUT_OF_RANGE", f"{location}/row_index"
    )
    if case_index in seen_cases:
        raise RelationError("RELATION_CASE_INDEX_DUPLICATE", f"{location}/case_index")
    if row_index in seen_rows:
        raise RelationError("RELATION_ROW_INDEX_DUPLICATE", f"{location}/row_index")
    seen_cases.add(case_index)
    seen_rows.add(row_index)
    row = rows[row_index]
    if row["slot_kind"] != "subject":
        raise RelationError("RELATION_CONTROL_MAPPED", f"{location}/row_index")
    declared_id = pair.get("case_id")
    case_id, item = cases[case_index]
    if type(declared_id) is not str or declared_id != case_id or declared_id != row["case_id"]:
        raise RelationError("RELATION_CASE_ID_MISMATCH", f"{location}/case_id")
    if item["availability"] != "observed" or row["category"] == "errors":
        finding = "value_comparison_ineligible"
    elif item["value"] is row["source_success"]:
        finding = "source_boolean_values_equal"
    else:
        finding = "source_boolean_values_different"
    return {
        "case_index": case_index,
        "row_index": row_index,
        "goppi_availability": item["availability"],
        "goppi_value": item["value"],
        "promptfoo_source_success": row["source_success"],
        "promptfoo_category": row["category"],
        "finding": finding,
    }


def review_declared_cases(
    goppi_items: object,
    goppi_case_ids: object,
    promptfoo_rows: object,
    pairs: object,
    coverage: object,
) -> dict[str, object]:
    """Review owner-declared one-to-one coordinates without inferring equivalence."""
    cases = _goppi_items(goppi_items, goppi_case_ids)
    rows, subject_indices = _promptfoo_rows(promptfoo_rows)
    if coverage not in ("complete", "partial"):
        raise RelationError("RELATION_COVERAGE_INVALID", "/coverage")
    declared = _list(pairs, "RELATION_PAIRS_INVALID", "/pairs")
    seen_cases: set[int] = set()
    seen_rows: set[int] = set()
    results = [
        _pair(value, index, cases, rows, seen_cases, seen_rows)
        for index, value in enumerate(declared)
    ]
    unmatched_cases = sorted(set(range(len(cases))) - seen_cases)
    unmatched_rows = sorted(subject_indices - seen_rows)
    if coverage == "complete" and (unmatched_cases or unmatched_rows):
        raise RelationError("RELATION_COMPLETE_COVERAGE_MISSING", "/coverage")
    counts = {"equal": 0, "different": 0, "ineligible": 0}
    for result in results:
        finding = cast(str, result["finding"])
        counts[_FINDING_KEYS[finding]] += 1
    return {
        "pairs": results,
        "unmatched_case_indices": unmatched_cases,
        "unmatched_subject_row_indices": unmatched_rows,
        "finding_counts": counts,
        "next_verification_tasks": [_NEXT_TASK] if unmatched_cases or unmatched_rows else [],
    }
