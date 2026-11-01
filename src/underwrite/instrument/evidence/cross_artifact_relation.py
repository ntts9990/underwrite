"""Pure relationship between declared C55 cases and Promptfoo subject rows.

The result describes source Boolean fields and missingness only. It makes no
claim that inputs, metrics, or producer execution are equivalent or authentic.
"""

from __future__ import annotations

from typing import Literal, TypedDict, cast

_AVAILABILITIES = frozenset({"observed", "missing", "abstained", "error"})
_PROMPTFOO_CATEGORIES = frozenset({"successes", "failures", "errors"})
_NEXT_TASK = "DECLARE_REMAINING_CASE_CORRESPONDENCE"
_FINDING_KEYS = {
    "source_boolean_values_equal": "equal",
    "source_boolean_values_different": "different",
    "value_comparison_ineligible": "ineligible",
}
CASE_ID_MAX_LENGTH = 256


def case_mapping_prerequisite(
    goppi_case_ids: list[str], subject_case_ids: list[str]
) -> tuple[str, str]:
    """Name the first possible owner action; matching IDs do not prove task equivalence."""
    if not subject_case_ids:
        return "missing_subject_rows", "PROVIDE_PROMPTFOO_SUBJECT_ROWS"
    if set(goppi_case_ids).isdisjoint(subject_case_ids):
        return "missing_matching_case_ids", "PROVIDE_MATCHING_SOURCE_CASE_IDS"
    return "needs_owner_correspondence", "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE"


class SourceCaseMetadata(TypedDict):
    case_id: str | None
    slot_kind: Literal["subject", "control"] | None
    blockers: list[dict[str, object]]


class SourceCaseReadiness(TypedDict):
    metadata: dict[str, object]
    subject_rows: int | None
    review_prerequisites: str
    next_verification_tasks: list[dict[str, object]]


def inspect_promptfoo_source_case_metadata(row: object, index: int) -> SourceCaseMetadata:
    """Inspect only owner-authored case roles, without copying source values."""
    location = f"/promptfoo/results/results/{index}"
    if type(row) is not dict:
        raise RelationError("RELATION_PROMPTFOO_ROW_INVALID", location)
    source = cast(dict[str, object], row)
    test_case = source.get("testCase")
    if type(test_case) is not dict:
        return {
            "case_id": None,
            "slot_kind": None,
            "blockers": [
                {
                    "row_index": index,
                    "code": "PROMPTFOO_CASE_METADATA_REQUIRED",
                    "location": location + "/testCase",
                }
            ],
        }
    metadata = cast(dict[str, object], test_case).get("metadata")
    if type(metadata) is not dict:
        return {
            "case_id": None,
            "slot_kind": None,
            "blockers": [
                {
                    "row_index": index,
                    "code": "PROMPTFOO_CASE_METADATA_REQUIRED",
                    "location": location + "/testCase/metadata",
                }
            ],
        }
    selected = cast(dict[str, object], metadata)
    case_id = selected.get("case_id")
    slot_kind = selected.get("slot_kind")
    valid_case_id = type(case_id) is str and bool(case_id) and len(case_id) <= CASE_ID_MAX_LENGTH
    valid_slot_kind = slot_kind in ("subject", "control")
    blockers: list[dict[str, object]] = []
    if not valid_case_id:
        blockers.append(
            {
                "row_index": index,
                "code": "PROMPTFOO_CASE_ID_REQUIRED",
                "location": location + "/testCase/metadata/case_id",
            }
        )
    if not valid_slot_kind:
        blockers.append(
            {
                "row_index": index,
                "code": "PROMPTFOO_SLOT_KIND_REQUIRED",
                "location": location + "/testCase/metadata/slot_kind",
            }
        )
    return {
        "case_id": cast(str, case_id) if valid_case_id else None,
        "slot_kind": slot_kind if valid_slot_kind else None,
        "blockers": blockers,
    }


def _task(
    code: str, owner: str, case_index: int | None = None, row_index: int | None = None
) -> dict[str, object]:
    return {
        "code": code,
        "owner": owner,
        "case_index": case_index,
        "row_index": row_index,
    }


def _source_review_tasks() -> list[dict[str, object]]:
    return [
        _task("VERIFY_GOPPI_PORTABLE_CLOSURE", "source_reviewer"),
        _task("VERIFY_SOURCE_PROVENANCE", "source_reviewer"),
    ]


def unmapped_case_verification_tasks(
    goppi_case_ids: list[str], subject_case_ids: list[str]
) -> list[dict[str, object]]:
    """Keep owner mapping prerequisites separate from source-review checks."""
    _, next_owner_action = case_mapping_prerequisite(goppi_case_ids, subject_case_ids)
    return [_task(next_owner_action, "evaluation_owner"), *_source_review_tasks()]


def declared_case_verification_tasks(
    pairs: list[dict[str, object]], remaining_tasks: list[str]
) -> list[dict[str, object]]:
    """Plan checks for declared source claims, preserving the relation's task codes."""
    tasks = [
        _task("VERIFY_INPUT_AND_METRIC_SEMANTICS", "evaluation_owner"),
        _task("PROVIDE_COMPARATOR_CONDITIONS", "evaluation_owner"),
    ]
    tasks.extend(_task(code, "evaluation_owner") for code in remaining_tasks)
    tasks.extend(
        _task(
            "INSPECT_INELIGIBLE_CASE_EVIDENCE",
            "calling_agent",
            cast(int, pair["case_index"]),
            cast(int, pair["row_index"]),
        )
        for pair in pairs
        if pair["finding"] == "value_comparison_ineligible"
    )
    return [*tasks, *_source_review_tasks()]


def review_source_case_readiness(
    goppi_case_ids: list[str], source_rows: list[object]
) -> SourceCaseReadiness:
    """Summarize owner metadata and the first safe task without joining cases."""
    inspected = [
        inspect_promptfoo_source_case_metadata(row, index) for index, row in enumerate(source_rows)
    ]
    blockers = [blocker for row in inspected for blocker in row["blockers"]]
    case_ids_complete = all(row["case_id"] is not None for row in inspected)
    roles_complete = all(row["slot_kind"] is not None for row in inspected)
    subject_rows = (
        sum(row["slot_kind"] == "subject" for row in inspected) if roles_complete else None
    )
    if blockers:
        prerequisite = "missing_owner_metadata"
        owner_tasks = [_task("PROVIDE_PROMPTFOO_CASE_METADATA", "evaluation_owner")]
    else:
        subject_case_ids = [
            cast(str, row["case_id"]) for row in inspected if row["slot_kind"] == "subject"
        ]
        prerequisite, next_owner_action = case_mapping_prerequisite(
            goppi_case_ids, subject_case_ids
        )
        owner_tasks = [_task(next_owner_action, "evaluation_owner")]
    if blockers and roles_complete and subject_rows == 0:
        owner_tasks.append(_task("PROVIDE_PROMPTFOO_SUBJECT_ROWS", "evaluation_owner"))
    return {
        "metadata": {
            "case_ids": "complete" if case_ids_complete else "incomplete",
            "row_roles": "source_declared" if roles_complete else "incomplete",
            "blockers": blockers,
        },
        "subject_rows": subject_rows,
        "review_prerequisites": prerequisite,
        "next_verification_tasks": [*owner_tasks, *_source_review_tasks()],
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
