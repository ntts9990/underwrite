"""Classify claims as screened, confirmed, indeterminate or rejected.

A hard failure or failed disqualifying claim rejects. Missing, warned, unmeasured
or failed validity claims remain indeterminate. All-pass evidence confirms only
on a declared held-out basis; elsewhere it screens. A measured failure with partial
availability stays a failure and records that limitation. Classification is not
merge or deployment authorization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

Basis = Literal["same_batch", "held_in", "held_out"]
Effect = Literal["disqualifying", "validity"]
Verdict = Literal["pass", "warn", "fail", "not_measured"]
Availability = Literal["full", "partial", "below_quorum"]
Classification = Literal["screened", "confirmed", "indeterminate", "rejected"]
ReasonCode = Literal[
    "hard_failure",
    "disqualifying_claim_failed",
    "validity_claim_failed",
    "missing_required_claim",
    "claim_warn",
    "claim_not_measured",
    "stage_screened",
    "stage_confirmed",
    "lifecycle_ineligible",
]
ClassifyErrorCode = Literal[
    "INVALID_BASIS",
    "INVALID_REQUIREMENT",
    "INVALID_HARD_FAILURE",
    "INVALID_READ",
    "READ_AVAILABILITY_INCONSISTENT",
    "UNEXPECTED_CLAIM",
]

BASES: Final[tuple[Basis, ...]] = ("same_batch", "held_in", "held_out")
EFFECTS: Final[tuple[Effect, ...]] = ("disqualifying", "validity")
VERDICTS: Final[tuple[Verdict, ...]] = ("pass", "warn", "fail", "not_measured")
AVAILABILITY: Final[tuple[Availability, ...]] = ("full", "partial", "below_quorum")
CLASSIFICATIONS: Final[tuple[Classification, ...]] = (
    "screened",
    "confirmed",
    "indeterminate",
    "rejected",
)
REASON_CODES: Final[tuple[ReasonCode, ...]] = (
    "hard_failure",
    "disqualifying_claim_failed",
    "validity_claim_failed",
    "missing_required_claim",
    "claim_warn",
    "claim_not_measured",
    "stage_screened",
    "stage_confirmed",
    "lifecycle_ineligible",
)
PARTIAL_FAIL: Final = "measured_fail_under_partial_availability"
# read.v1's two read shapes: a measured read is full or partial (partial lowers pass);
# a not-measured read may sit at any state (above quorum it is a panel-less unknown).
CONSISTENT_READS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("pass", "full"),
        ("warn", "full"),
        ("fail", "full"),
        ("warn", "partial"),
        ("fail", "partial"),
        ("not_measured", "full"),
        ("not_measured", "partial"),
        ("not_measured", "below_quorum"),
    }
)


class ClassifyInputError(ValueError):
    """Input outside the vocabulary; no classification is produced."""

    def __init__(self, code: ClassifyErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class EvidenceClassification:
    """A classification, its reasons in precedence order, and the limitations that apply."""

    classification: Classification
    reason_codes: tuple[ReasonCode, ...]
    limitations: tuple[str, ...]


def _require(condition: bool, code: ClassifyErrorCode, detail: str) -> None:
    if not condition:
        raise ClassifyInputError(code, detail)


def _found(
    requirements: Sequence[tuple[str, Effect]],
    reads: Mapping[str, tuple[Verdict, Availability]],
) -> set[str]:
    """Which claim summary flags hold (``missing``, ``warn``, ``fail:<effect>``...)."""
    ids = [claim_id for claim_id, _ in requirements]
    _require(ids != [] and len(set(ids)) == len(ids), "INVALID_REQUIREMENT", "claim ids")
    _require(set(reads) <= set(ids), "UNEXPECTED_CLAIM", ",".join(sorted(set(reads) - set(ids))))
    found: set[str] = set()
    for claim_id, effect in requirements:
        _require(effect in EFFECTS, "INVALID_REQUIREMENT", claim_id)
        read = reads.get(claim_id)
        if read is None:
            found.add("missing")
            continue
        pair = type(read) is tuple and len(read) == 2  # noqa: PLR2004 -- (verdict, state)
        _require(pair, "INVALID_READ", claim_id)
        verdict, availability = read
        _require(verdict in VERDICTS and availability in AVAILABILITY, "INVALID_READ", claim_id)
        _require(read in CONSISTENT_READS, "READ_AVAILABILITY_INCONSISTENT", claim_id)
        found.add(f"fail:{effect}" if verdict == "fail" else verdict)
        if verdict == "fail" and availability == "partial":
            found.add(PARTIAL_FAIL)
    return found


def classify_evidence(
    basis: Basis,
    requirements: Sequence[tuple[str, Effect]],
    reads: Mapping[str, tuple[Verdict, Availability]],
    *,
    hard_failure: bool,
) -> EvidenceClassification:
    """Classify one basis from ``(claim_id, effect)`` requirements and per-claim reads.

    ``reads`` maps a claim id to its read's ``(verdict, availability state)``; an absent
    claim is a missing read. Binding a read to a claim is the caller's duty.
    """
    _require(basis in BASES, "INVALID_BASIS", str(basis))
    _require(type(hard_failure) is bool, "INVALID_HARD_FAILURE", repr(hard_failure))
    found = _found(requirements, reads)
    flags: dict[ReasonCode, bool] = {
        "hard_failure": hard_failure,
        "disqualifying_claim_failed": "fail:disqualifying" in found,
        "validity_claim_failed": "fail:validity" in found,
        "missing_required_claim": "missing" in found,
        "claim_warn": "warn" in found,
        "claim_not_measured": "not_measured" in found,
    }
    reasons: tuple[ReasonCode, ...] = tuple(c for c in REASON_CODES if flags.get(c))
    limitations = (PARTIAL_FAIL,) if PARTIAL_FAIL in found else ()
    if hard_failure or flags["disqualifying_claim_failed"]:
        return EvidenceClassification("rejected", reasons, limitations)
    if reasons:
        return EvidenceClassification("indeterminate", reasons, limitations)
    if basis == "held_out":
        return EvidenceClassification("confirmed", ("stage_confirmed",), limitations)
    return EvidenceClassification("screened", ("stage_screened",), limitations)

