"""Compare concrete supplied Boolean checks; do not run or certify evaluators."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

CheckState = Literal["matched", "mismatched", "not_measured"]
Support = Literal["available", "not_measured"]


class SelfCheckError(ValueError):
    """Malformed observations cannot yield a partial check summary."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class CheckObservation:
    """Expected/observed facts with caller-supplied, unverified provenance."""

    check_id: str
    expected: bool
    observed: bool | None
    source_ref: str
    source_digest: str


@dataclass(frozen=True)
class CheckResult:
    """One concrete comparison, with missing distinct from mismatch."""

    check_id: str
    expected: bool
    observed: bool | None
    source_ref: str
    source_digest: str

    @property
    def state(self) -> CheckState:
        return _check_state(self)


def _check_state(row: CheckResult) -> CheckState:
    if row.observed is None:
        return "not_measured"
    return "matched" if row.expected == row.observed else "mismatched"


@dataclass(frozen=True)
class SelfCheckEvidence:
    """Observations, not a heterogeneous five-rung quality/approval aggregate."""

    checks: tuple[CheckResult, ...]
    limitations: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def observed(self) -> int:
        return _observed(self)

    @property
    def mismatches(self) -> int:
        return _mismatches(self)

    @property
    def missing(self) -> int:
        return _missing(self)

    @property
    def reason(self) -> str | None:
        return _reason(self)

    @property
    def support(self) -> Support:
        return _support(self)


def _observed(evidence: SelfCheckEvidence) -> int:
    return sum(row.observed is not None for row in evidence.checks)


def _mismatches(evidence: SelfCheckEvidence) -> int:
    return sum(row.state == "mismatched" for row in evidence.checks)


def _missing(evidence: SelfCheckEvidence) -> int:
    return evidence.total - evidence.observed


def _reason(evidence: SelfCheckEvidence) -> str | None:
    if not evidence.checks:
        return "empty_population"
    return "missing_observations" if evidence.missing else None


def _support(evidence: SelfCheckEvidence) -> Support:
    return "not_measured" if evidence.reason else "available"


def _text(value: str, code: str) -> None:
    if type(value) is not str or not value.strip() or not unicodedata.is_normalized("NFC", value):
        raise SelfCheckError(code)
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise SelfCheckError(code) from exc


def _validate(row: CheckObservation) -> None:
    if type(row) is not CheckObservation:
        raise SelfCheckError("INVALID_CHECK")
    _text(row.check_id, "INVALID_CHECK_ID")
    _text(row.source_ref, "INVALID_SOURCE_REF")
    if (
        type(row.source_digest) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", row.source_digest) is None
    ):
        raise SelfCheckError("INVALID_SOURCE_DIGEST", row.check_id)
    if type(row.expected) is not bool or (
        row.observed is not None and type(row.observed) is not bool
    ):
        raise SelfCheckError("INVALID_BOOLEAN", row.check_id)


def compare_selfcheck(checks: Sequence[CheckObservation]) -> SelfCheckEvidence:
    """Validate the whole population before comparing supplied observations."""
    if type(checks) not in (list, tuple):
        raise SelfCheckError("INVALID_POPULATION")
    seen: set[str] = set()
    for row in checks:
        _validate(row)
        if row.check_id in seen:
            raise SelfCheckError("DUPLICATE_CHECK_ID", row.check_id)
        seen.add(row.check_id)
    return SelfCheckEvidence(
        tuple(
            CheckResult(row.check_id, row.expected, row.observed, row.source_ref, row.source_digest)
            for row in sorted(checks, key=lambda row: row.check_id)
        ),
        (
            "supplied_provenance_not_independently_verified",
            "no_evaluator_execution_or_quality_authority",
        ),
    )
