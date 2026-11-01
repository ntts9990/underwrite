"""Check selected C55 assessment/result consistency without bundle provenance."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from typing import cast

_ASSESSMENT_SCHEMA = "goppi.operator_answer_exchange_assessment.v1"
_SOURCE_FORMAT = "goppi.operator_answer_assessment"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_CASES = 40
_OPERATIONAL_REASONS = frozenset(
    {
        "native_unavailable",
        "verifier_timeout",
        "verifier_exit_error",
        "verifier_protocol_error",
        "verifier_identity_mismatch",
        "verifier_output_limit",
        "verifier_process_failure",
        "cleanup_unresolved",
    }
)
_REASONS = {
    "verifier_binary_true": frozenset({"binary_true"}),
    "verifier_binary_false": frozenset({"binary_false"}),
    "verifier_unknown": frozenset({"verifier_unknown"}),
    "verifier_indeterminate": frozenset({"verifier_indeterminate"}),
    "operational_failure": _OPERATIONAL_REASONS,
    "not_provided": frozenset({"not_provided"}),
    "not_started": frozenset(
        {"not_started_after_control_mismatch", "not_started_after_operational_failure"}
    ),
}
_PROJECTIONS: dict[str, tuple[str, bool | None, str | None, str]] = {
    "verifier_binary_true": ("observed", True, None, "none"),
    "verifier_binary_false": ("observed", False, None, "none"),
    "verifier_unknown": ("abstained", None, "SOURCE_ABSTAINED", "inspect_source"),
    "verifier_indeterminate": ("abstained", None, "SOURCE_ABSTAINED", "inspect_source"),
    "operational_failure": ("error", None, "SOURCE_ERROR", "inspect_source"),
    "not_provided": ("missing", None, "SOURCE_MISSING", "inspect_source"),
    "not_started": ("missing", None, "SOURCE_MISSING", "inspect_source"),
}
_COUNT_FIELDS = {
    "verifier_binary_true": "binary_true",
    "verifier_binary_false": "binary_false",
    "verifier_unknown": "unknown",
    "verifier_indeterminate": "indeterminate",
    "operational_failure": "operational_failure",
    "not_started": "not_started",
    "not_provided": "cases_not_provided",
}


class C55SelectedError(ValueError):
    """Finite selected-file refusal without raw source content in the message."""

    def __init__(self, reason: str, location: str) -> None:
        self.reason, self.location = reason, location
        super().__init__(reason)


def _mapping(value: object, reason: str, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise C55SelectedError(reason, location)
    mapping = cast(Mapping[object, object], value)
    if any(type(key) is not str for key in mapping):
        raise C55SelectedError(reason, location)
    return cast(Mapping[str, object], mapping)


def _list(value: object, reason: str, location: str) -> list[object]:
    if type(value) is not list:
        raise C55SelectedError(reason, location)
    return cast(list[object], value)


def _exact(value: object, expected: object, reason: str, location: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise C55SelectedError(reason, location)


def _assessment_cases(assessment: Mapping[str, object]) -> list[tuple[str, str]]:
    _exact(
        assessment.get("schema"),
        _ASSESSMENT_SCHEMA,
        "C55_PROFILE_UNSUPPORTED",
        "/assessment/schema",
    )
    raw_cases = _list(assessment.get("cases"), "C55_CASES_INVALID", "/assessment/cases")
    if not 1 <= len(raw_cases) <= _MAX_CASES:
        raise C55SelectedError("C55_CASES_INVALID", "/assessment/cases")
    cases: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(raw_cases):
        location = f"/assessment/cases/{index}"
        case = _mapping(value, "C55_CASE_INVALID", location)
        case_id = case.get("case_id")
        if type(case_id) is not str or not case_id or case_id in seen_ids:
            raise C55SelectedError("C55_CASE_ID_INVALID", f"{location}/case_id")
        disposition, reason = case.get("disposition"), case.get("reason_code")
        if type(disposition) is not str or disposition not in _REASONS:
            raise C55SelectedError("C55_DISPOSITION_INVALID", f"{location}/disposition")
        if type(reason) is not str or reason not in _REASONS[disposition]:
            raise C55SelectedError("C55_REASON_MISMATCH", f"{location}/reason_code")
        seen_ids.add(case_id)
        cases.append((case_id, disposition))
    _assessment_counts(assessment.get("counts"), cases)
    return cases


def _assessment_counts(value: object, cases: list[tuple[str, str]]) -> None:
    counts = _mapping(value, "C55_COUNTS_INVALID", "/assessment/counts")
    _exact(
        counts.get("count_scope"),
        "terminal_complete",
        "C55_COUNTS_INVALID",
        "/assessment/counts/count_scope",
    )
    by_disposition = Counter(disposition for _, disposition in cases)
    expected = {field: by_disposition[disposition] for disposition, field in _COUNT_FIELDS.items()}
    expected.update(
        cases_planned=len(cases),
        submissions_provided=len(cases) - by_disposition["not_provided"],
    )
    for field, count in expected.items():
        _exact(counts.get(field), count, "C55_COUNTS_MISMATCH", f"/assessment/counts/{field}")


def _result_header(result: Mapping[str, object], assessment_hash: str, count: int) -> None:
    if type(assessment_hash) is not str or _HASH.fullmatch(assessment_hash) is None:
        raise C55SelectedError("C55_ASSESSMENT_HASH_INVALID", "/assessment_hash")
    _exact(
        result.get("schema"),
        "evidence_result.v1",
        "C55_RESULT_PROFILE_UNSUPPORTED",
        "/result/schema",
    )
    source = _mapping(result.get("source"), "C55_RESULT_SOURCE_INVALID", "/result/source")
    for field, expected in (("format", _SOURCE_FORMAT), ("format_version", "v1")):
        _exact(source.get(field), expected, "C55_RESULT_SOURCE_MISMATCH", f"/result/source/{field}")
    identity = _mapping(
        source.get("identity"), "C55_RESULT_SOURCE_INVALID", "/result/source/identity"
    )
    for field, expected in (("kind", "single_file"), ("raw_hash", assessment_hash)):
        _exact(
            identity.get(field),
            expected,
            "C55_RESULT_SOURCE_MISMATCH",
            f"/result/source/identity/{field}",
        )
    processing = _mapping(
        result.get("processing"), "C55_RESULT_PROCESSING_INVALID", "/result/processing"
    )
    for field, expected in (("state", "analyzed"), ("reason", None), ("next_action", "none")):
        _exact(
            processing.get(field),
            expected,
            "C55_RESULT_PROCESSING_MISMATCH",
            f"/result/processing/{field}",
        )
    population = _mapping(
        result.get("population"), "C55_RESULT_POPULATION_INVALID", "/result/population"
    )
    for field, expected in (("unit", "case"), ("observed_count", count), ("planned_count", count)):
        _exact(
            population.get(field),
            expected,
            "C55_RESULT_POPULATION_MISMATCH",
            f"/result/population/{field}",
        )
    _exact(
        result.get("verification"), [], "C55_RESULT_VERIFICATION_MISMATCH", "/result/verification"
    )


def _projected_item(value: object, index: int, disposition: str) -> dict[str, object]:
    location = f"/result/items/{index}"
    item = _mapping(value, "C55_RESULT_ITEM_INVALID", location)
    coordinate = _mapping(
        item.get("coordinate"), "C55_RESULT_ITEM_MISMATCH", f"{location}/coordinate"
    )
    for field, expected in (("kind", "case"), ("index", index)):
        _exact(
            coordinate.get(field),
            expected,
            "C55_RESULT_ITEM_MISMATCH",
            f"{location}/coordinate/{field}",
        )
    metric = _mapping(item.get("metric"), "C55_RESULT_ITEM_MISMATCH", f"{location}/metric")
    for field, expected in (("semantic_binding", "unbound"), ("source_metric_index", 0)):
        _exact(
            metric.get(field),
            expected,
            "C55_RESULT_ITEM_MISMATCH",
            f"{location}/metric/{field}",
        )
    expected_fields: dict[str, object] = {
        "applicability": "applicable",
        "origin": "source_claim",
        "method": None,
        "policy": None,
        "permitted_effect": "descriptive",
    }
    availability, result_value, reason, action = _PROJECTIONS[disposition]
    expected_fields.update(
        availability=availability, value=result_value, reason=reason, next_action=action
    )
    for field, expected in expected_fields.items():
        _exact(item.get(field), expected, "C55_RESULT_ITEM_MISMATCH", f"{location}/{field}")
    return {"availability": availability, "value": result_value}


def validate_selected_c55_projection(
    assessment: Mapping[str, object],
    result: Mapping[str, object],
    assessment_hash: str,
) -> tuple[list[str], list[dict[str, object]]]:
    """Validate selected bound fields and return case IDs with normalized items.

    The selected-file check does not verify Goppi's complete five-file closure,
    keyed local authentication, or the truth of the producer's source claims.
    """
    cases = _assessment_cases(assessment)
    _result_header(result, assessment_hash, len(cases))
    raw_items = _list(result.get("items"), "C55_RESULT_ITEMS_INVALID", "/result/items")
    if len(raw_items) != len(cases):
        raise C55SelectedError("C55_RESULT_ITEM_COUNT_MISMATCH", "/result/items")
    items = [_projected_item(item, index, cases[index][1]) for index, item in enumerate(raw_items)]
    return [case_id for case_id, _ in cases], items
