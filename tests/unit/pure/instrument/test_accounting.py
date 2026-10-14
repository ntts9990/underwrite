"""Promptfoo count agreement is descriptive and fails closed on unusable counters."""

from __future__ import annotations

import math

import pytest
from underwrite_core.canonical import SAFE_INT_MAX

from underwrite.instrument.evidence.accounting import (
    ROW_LIMIT,
    AccountingError,
    audit_promptfoo,
    audit_promptfoo_with_rows,
)


def test_exclusive_rows_preserve_zero_and_do_not_infer_error_from_text() -> None:
    rows = [
        {"success": True, "failureReason": 0, "score": 0, "error": "source text"},
        {"success": False, "failureReason": 1, "score": 0},
        {"success": False, "failureReason": 2, "score": 1, "gradingResult": {"pass": True}},
    ]
    result = audit_promptfoo({"successes": 1, "failures": 1, "errors": 1}, rows)
    assert result["row_tallies"] == {"successes": 1, "failures": 1, "errors": 1, "total": 3}
    assert result["deltas"] == {"successes": 0, "failures": 0, "errors": 0, "total": 0}
    assert result["declared_relation"] == "consistent" and result["findings"] == []


def test_per_category_deltas_expose_mismatch_even_when_totals_agree() -> None:
    rows = [
        {"success": True, "failureReason": 0},
        {"success": False, "failureReason": 1},
        {"success": False, "failureReason": 2},
    ]
    result = audit_promptfoo({"successes": 2, "failures": 1, "errors": 0}, rows)
    assert result["producer_stats"]["total"] == result["row_tallies"]["total"] == len(rows)
    assert result["deltas"] == {"successes": -1, "failures": 0, "errors": 1, "total": 0}
    assert result["declared_relation"] == "inconsistent"
    assert result["findings"] == [
        {
            "code": "CATEGORY_COUNT_MISMATCH",
            "row_index": None,
            "location": "/results/stats/successes",
        },
        {"code": "CATEGORY_COUNT_MISMATCH", "row_index": None, "location": "/results/stats/errors"},
    ]


def test_only_verified_source_flag_conflicts_are_findings() -> None:
    rows = [
        {"success": True, "failureReason": 1, "gradingResult": {"pass": True}},
        {"success": False, "failureReason": 1, "gradingResult": {"pass": True}},
        {"success": False, "failureReason": 0, "gradingResult": None},
        {"success": False, "failureReason": 2, "gradingResult": {"pass": True}},
    ]
    result = audit_promptfoo({"successes": 1, "failures": 2, "errors": 1}, rows)
    assert result["deltas"]["total"] == 0
    assert result["declared_relation"] == "inconsistent"
    assert result["findings"] == [
        {
            "code": "SOURCE_FLAGS_DISAGREE",
            "row_index": 0,
            "location": "/results/results/0/failureReason",
        },
        {
            "code": "SOURCE_FLAGS_DISAGREE",
            "row_index": 1,
            "location": "/results/results/1/gradingResult/pass",
        },
    ]
    assert len(result["findings"]) <= len(rows) + 3


@pytest.mark.parametrize(
    "value",
    [-1, -1.0, True, 1.5, None, "1", math.nan, math.inf, SAFE_INT_MAX + 1, float(SAFE_INT_MAX + 2)],
)
def test_direct_counter_inputs_reject_invalid_numbers(value: object) -> None:
    with pytest.raises(AccountingError, match="INVALID_COUNT"):
        audit_promptfoo({"successes": value, "failures": 0, "errors": 0}, [])


def test_direct_total_overflow_is_refused() -> None:
    boundary = audit_promptfoo({"successes": SAFE_INT_MAX, "failures": 0, "errors": 0}, [])
    assert boundary["producer_stats"]["total"] == SAFE_INT_MAX
    with pytest.raises(AccountingError, match="COUNT_TOTAL_OVERFLOW"):
        audit_promptfoo({"successes": SAFE_INT_MAX, "failures": 1, "errors": 0}, [])


@pytest.mark.parametrize(
    "row",
    [
        {"success": 1, "failureReason": 0},
        {"success": False, "failureReason": True},
        {"success": False, "failureReason": 3},
        {"success": False, "failureReason": 1, "gradingResult": {"pass": 1}},
        {"success": False, "failureReason": math.nan},
    ],
)
def test_direct_row_inputs_reject_invalid_flags(row: object) -> None:
    with pytest.raises(AccountingError, match="INVALID_ROW"):
        audit_promptfoo({"successes": 0, "failures": 1, "errors": 0}, [row])


def test_row_limit_is_checked_before_row_traversal() -> None:
    with pytest.raises(AccountingError, match="ROW_LIMIT_EXCEEDED"):
        audit_promptfoo({"successes": 0, "failures": 0, "errors": 0}, [object()] * (ROW_LIMIT + 1))


def test_integral_float_counters_are_normalized_for_original_projection_consumers() -> None:
    result = audit_promptfoo(
        {"successes": 1.0, "failures": 0.0, "errors": -0.0}, [{"success": True, "failureReason": 0}]
    )
    assert result["declared_relation"] == "consistent"
    assert all(type(value) is int for value in result["producer_stats"].values())


def test_row_index_preserves_source_flags_and_one_classification() -> None:
    rows = [
        {"success": True, "failureReason": 2, "score": 0},
        {"success": True, "failureReason": 0, "score": 0.3},
        {"success": False, "failureReason": 1, "gradingResult": {"pass": True}},
        {"success": False, "failureReason": 0, "gradingResult": None},
        {"success": False, "failureReason": 2},
    ]
    stats = {"successes": 2, "failures": 2, "errors": 1}
    accounting, indexed = audit_promptfoo_with_rows(stats, rows)
    assert accounting == audit_promptfoo(stats, rows)
    assert indexed == [
        {"row_index": 0, "category": "successes", "source_success": True,
         "source_failure_reason": 2, "grading_pass": None},
        {"row_index": 1, "category": "successes", "source_success": True,
         "source_failure_reason": 0, "grading_pass": None},
        {"row_index": 2, "category": "failures", "source_success": False,
         "source_failure_reason": 1, "grading_pass": True},
        {"row_index": 3, "category": "failures", "source_success": False,
         "source_failure_reason": 0, "grading_pass": None},
        {"row_index": 4, "category": "errors", "source_success": False,
         "source_failure_reason": 2, "grading_pass": None},
    ]
    assert [item["row_index"] for item in accounting["findings"]] == [0, 2]
