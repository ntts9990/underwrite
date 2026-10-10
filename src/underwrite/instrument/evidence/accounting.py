"""Bounded, descriptive Promptfoo count checks over already supplied source claims."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict, cast

from underwrite_core.canonical import SAFE_INT_MAX

ROW_LIMIT = 2000
CATEGORIES = ("successes", "failures", "errors")
REASON_NONE = 0
REASON_ASSERT = 1
REASON_ERROR = 2


class AccountingError(ValueError):
    """Input cannot support this bounded audit; never a successful finding."""


class Counts(TypedDict):
    successes: int
    failures: int
    errors: int
    total: int


class Finding(TypedDict):
    code: Literal["CATEGORY_COUNT_MISMATCH", "SOURCE_FLAGS_DISAGREE"]
    row_index: int | None
    location: str


class AccountingResult(TypedDict):
    producer_stats: Counts
    row_tallies: Counts
    deltas: Counts
    declared_relation: Literal["consistent", "inconsistent"]
    findings: list[Finding]


Category = Literal["successes", "failures", "errors"]


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= SAFE_INT_MAX:
        raise AccountingError("INVALID_COUNT")
    return value


def _counts(successes: int, failures: int, errors: int) -> Counts:
    total = successes + failures + errors
    if total > SAFE_INT_MAX:
        raise AccountingError("COUNT_TOTAL_OVERFLOW")
    return {"successes": successes, "failures": failures, "errors": errors, "total": total}


def _classify_row(row: object, index: int) -> tuple[Category, Finding | None]:
    if not isinstance(row, Mapping):
        raise AccountingError("INVALID_ROW")
    fields = cast(Mapping[str, object], row)
    success, reason = fields.get("success"), fields.get("failureReason")
    if (
        type(success) is not bool
        or type(reason) is not int
        or reason not in (REASON_NONE, REASON_ASSERT, REASON_ERROR)
    ):
        raise AccountingError("INVALID_ROW")
    category: Category = (
        "successes" if success else "errors" if reason == REASON_ERROR else "failures"
    )
    grading = fields.get("gradingResult")
    if grading is not None:
        if not isinstance(grading, Mapping):
            raise AccountingError("INVALID_ROW")
        grading_pass = cast(Mapping[str, object], grading).get("pass")
        if type(grading_pass) is not bool:
            raise AccountingError("INVALID_ROW")
    else:
        grading_pass = None
    location: str | None = None
    if success and reason != REASON_NONE:
        location = f"/results/results/{index}/failureReason"
    elif reason == REASON_ASSERT and grading_pass is True:
        location = f"/results/results/{index}/gradingResult/pass"
    finding: Finding | None = (
        None
        if location is None
        else {"code": "SOURCE_FLAGS_DISAGREE", "row_index": index, "location": location}
    )
    return category, finding


def audit_promptfoo(stats: object, rows: object) -> AccountingResult:
    """Compare declared counters with exclusive row classes, not execution truth.

    A successful row counts as success. Otherwise Promptfoo's ERROR reason counts
    as error; every other supported reason counts as failure. Scores and error
    strings cannot turn a row into an error or establish a cause.
    """
    if not isinstance(stats, Mapping) or not isinstance(rows, list | tuple):
        raise AccountingError("INVALID_INPUT")
    source_stats = cast(Mapping[str, object], stats)
    source_rows = cast(list[object] | tuple[object, ...], rows)
    if len(source_rows) > ROW_LIMIT:
        raise AccountingError("ROW_LIMIT_EXCEEDED")
    producer = _counts(
        _count(source_stats.get("successes")),
        _count(source_stats.get("failures")),
        _count(source_stats.get("errors")),
    )
    tallies = {key: 0 for key in CATEGORIES}
    findings: list[Finding] = []
    for index, row in enumerate(source_rows):
        category, finding = _classify_row(row, index)
        tallies[category] += 1
        if finding is not None:
            findings.append(finding)
    observed = _counts(tallies["successes"], tallies["failures"], tallies["errors"])
    deltas: Counts = {
        "successes": observed["successes"] - producer["successes"],
        "failures": observed["failures"] - producer["failures"],
        "errors": observed["errors"] - producer["errors"],
        "total": observed["total"] - producer["total"],
    }
    for category in CATEGORIES:
        if deltas[category]:
            findings.append(
                {
                    "code": "CATEGORY_COUNT_MISMATCH",
                    "row_index": None,
                    "location": f"/results/stats/{category}",
                }
            )
    return {
        "producer_stats": producer,
        "row_tallies": observed,
        "deltas": deltas,
        "declared_relation": "inconsistent" if findings else "consistent",
        "findings": findings,
    }
