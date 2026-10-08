"""Conditional random-halves observations using the current mean-signflip engine.
This does not establish independent trials, sound scoring, or calibration."""

from __future__ import annotations

import math
import random
import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, cast

from underwrite.instrument.evidence.paired import (
    EXACT_MAX_CASES,
    MIN_PERMUTATION_ITERATIONS,
    PermutationEvidence,
    permutation_evidence,
)

Detectability = Literal["vacuous", "detectable_exact", "unresolved"]
Support = Literal["available", "not_measured"]
_TWO_ARMS = 2


class AAError(ValueError):
    """Malformed supplied facts or exceeded reserved traversal limit."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class AACase:
    """One recorded case under a caller-bound condition and scoring policy."""

    case_id: str
    outcomes: tuple[bool, ...]
    diagnostic: bool


@dataclass(frozen=True)
class AAPolicy:
    """Explicit diagnostic settings; minima are not scientific adequacy claims."""

    seed: int
    iterations: int
    permutation_iterations: int
    significance_level: float
    minimum_cases: int
    minimum_iterations: int
    max_work: int


@dataclass(frozen=True)
class AAIteration:
    """Minimal observation; derived RNG seeds and regrouped raw rows stay local."""

    p_value: float
    minimum_attainable_p: float | None
    resolution_floor: float | None
    detectability: Detectability
    non_tied_comparisons: int
    completed_permutation_iterations: int


@dataclass(frozen=True)
class AAEvidence:
    """Descriptive counts and availability, never a calibration/pass badge."""

    policy: AAPolicy
    usable_case_ids: tuple[str, ...]
    diagnostic_case_ids: tuple[str, ...]
    insufficient_repeat_case_ids: tuple[str, ...]
    usable_repeats: int
    odd_repeats_per_iteration: int
    iterations: tuple[AAIteration, ...]
    reserved_work: int
    absence_reason: str | None
    limitations: tuple[str, ...]
    method: Literal["aa-random-halves-mean-signflip-v1"] = field(
        default="aa-random-halves-mean-signflip-v1", init=False
    )

    @property
    def requested_iterations(self) -> int:
        return self.policy.iterations

    @property
    def max_work(self) -> int:
        return self.policy.max_work

    @property
    def performed_iterations(self) -> int:
        return len(self.iterations)

    @property
    def usable_cases(self) -> int:
        return len(self.usable_case_ids)

    @property
    def excluded_diagnostic_cases(self) -> int:
        return len(self.diagnostic_case_ids)

    @property
    def excluded_insufficient_repeat_cases(self) -> int:
        return len(self.insufficient_repeat_case_ids)

    @property
    def input_cases(self) -> int:
        return _input_cases(self)

    @property
    def dropped_odd_repeats(self) -> int:
        return _dropped_odd_repeats(self)

    @property
    def significant_iterations(self) -> int:
        return _significant_iterations(self)

    @property
    def vacuous_iterations(self) -> int:
        return _vacuous_iterations(self)

    @property
    def detectable_exact_iterations(self) -> int:
        return _detectable_exact_iterations(self)

    @property
    def unresolved_detectability_iterations(self) -> int:
        return _unresolved_detectability_iterations(self)

    @property
    def non_tied_comparisons(self) -> int:
        return _non_tied_comparisons(self)

    @property
    def tied_comparisons(self) -> int:
        return _tied_comparisons(self)

    @property
    def tie_rate(self) -> float | None:
        return _tie_rate(self)

    @property
    def false_positive_rate(self) -> float | None:
        return _false_positive_rate(self)

    @property
    def known_vacuous_fraction(self) -> float | None:
        return _known_vacuous_fraction(self)

    @property
    def vacuous_rate_reason(self) -> str | None:
        return _vacuous_rate_reason(self)

    @property
    def vacuous_rate(self) -> float | None:
        return None if self.vacuous_rate_reason else self.known_vacuous_fraction

    @property
    def p_value_summary(self) -> tuple[float, float, float] | None:
        return _p_value_summary(self)

    @property
    def reason(self) -> str | None:
        return _reason(self)

    @property
    def support(self) -> Support:
        return _support(self)


def _input_cases(evidence: AAEvidence) -> int:
    """Every case the caller supplied, including the two kinds this engine set aside."""
    return (
        evidence.usable_cases
        + evidence.excluded_diagnostic_cases
        + evidence.excluded_insufficient_repeat_cases
    )


def _dropped_odd_repeats(evidence: AAEvidence) -> int:
    return evidence.odd_repeats_per_iteration * evidence.performed_iterations


def _comparisons(evidence: AAEvidence) -> int:
    """Every case-by-iteration comparison this run could have made."""
    return evidence.usable_cases * evidence.performed_iterations


def _significant_iterations(evidence: AAEvidence) -> int:
    alpha = evidence.policy.significance_level
    return sum(row.p_value < alpha for row in evidence.iterations)


def _with_detectability(evidence: AAEvidence, detectability: Detectability) -> int:
    """Count iterations with the requested detectability verdict."""
    return sum(row.detectability == detectability for row in evidence.iterations)


def _vacuous_iterations(evidence: AAEvidence) -> int:
    return _with_detectability(evidence, "vacuous")


def _detectable_exact_iterations(evidence: AAEvidence) -> int:
    return _with_detectability(evidence, "detectable_exact")


def _unresolved_detectability_iterations(evidence: AAEvidence) -> int:
    return _with_detectability(evidence, "unresolved")


def _non_tied_comparisons(evidence: AAEvidence) -> int:
    return sum(row.non_tied_comparisons for row in evidence.iterations)


def _tied_comparisons(evidence: AAEvidence) -> int:
    return _comparisons(evidence) - evidence.non_tied_comparisons


def _tie_rate(evidence: AAEvidence) -> float | None:
    total = _comparisons(evidence)
    return evidence.tied_comparisons / total if total else None


def _false_positive_rate(evidence: AAEvidence) -> float | None:
    if not evidence.iterations:
        return None
    return evidence.significant_iterations / evidence.performed_iterations


def _known_vacuous_fraction(evidence: AAEvidence) -> float | None:
    if not evidence.iterations:
        return None
    return evidence.vacuous_iterations / evidence.performed_iterations


def _vacuous_rate_reason(evidence: AAEvidence) -> str | None:
    """Why a vacuous rate would mislead: unresolved iterations outrank a run-wide absence."""
    if evidence.unresolved_detectability_iterations:
        return "unresolved_detectability"
    return evidence.absence_reason


def _p_value_summary(evidence: AAEvidence) -> tuple[float, float, float] | None:
    if not evidence.iterations:
        return None
    values = tuple(row.p_value for row in evidence.iterations)
    return min(values), statistics.median(values), max(values)


def _reason(evidence: AAEvidence) -> str | None:
    """Why this evidence is unavailable, most specific cause first, or None.

    A run-wide absence outranks per-iteration detectability, and one unresolved
    iteration outranks wholly vacuous ones: a run is only called vacuous when every
    performed iteration was, so a partial result is never folded into a total one.
    """
    if evidence.absence_reason:
        return evidence.absence_reason
    if evidence.unresolved_detectability_iterations:
        return "unresolved_detectability"
    if evidence.vacuous_iterations == evidence.performed_iterations:
        return "vacuous"
    return None


def _support(evidence: AAEvidence) -> Support:
    return "not_measured" if evidence.reason else "available"


def _policy(policy: AAPolicy) -> None:
    if type(policy) is not AAPolicy:
        raise AAError("INVALID_POLICY")
    if type(policy.seed) is not int:
        raise AAError("INVALID_SEED")
    for name, value, minimum in (
        ("ITERATIONS", policy.iterations, 1),
        ("PERMUTATION_ITERATIONS", policy.permutation_iterations, MIN_PERMUTATION_ITERATIONS),
        ("MINIMUM_CASES", policy.minimum_cases, 2),
        ("MINIMUM_ITERATIONS", policy.minimum_iterations, 1),
        ("MAX_WORK", policy.max_work, 1),
    ):
        if type(value) is not int or value < minimum:
            raise AAError(f"INVALID_{name}")
    alpha = policy.significance_level
    if type(alpha) is not float or not math.isfinite(alpha) or not 0 < alpha < 1:
        raise AAError("INVALID_SIGNIFICANCE_LEVEL")


def _cases(cases: Sequence[AACase]) -> tuple[AACase, ...]:
    if type(cases) not in (list, tuple):
        raise AAError("INVALID_POPULATION")
    seen: set[str] = set()
    for row in cases:
        if type(row) is not AACase:
            raise AAError("INVALID_CASE")
        identifier = row.case_id
        if (
            type(identifier) is not str
            or not identifier.strip()
            or not unicodedata.is_normalized("NFC", identifier)
        ):
            raise AAError("INVALID_CASE_ID")
        try:
            identifier.encode("utf-8")
        except UnicodeError as exc:
            raise AAError("INVALID_CASE_ID") from exc
        if identifier in seen:
            raise AAError("DUPLICATE_CASE_ID", identifier)
        seen.add(identifier)
        if type(row.diagnostic) is not bool:
            raise AAError("INVALID_DIAGNOSTIC", identifier)
        if type(row.outcomes) is not tuple or any(
            type(value) is not bool for value in row.outcomes
        ):
            raise AAError("INVALID_OUTCOMES", identifier)
    return tuple(sorted(cases, key=lambda row: row.case_id))


def _absence(rows: tuple[AACase, ...], usable: tuple[AACase, ...], policy: AAPolicy) -> str | None:
    if not rows:
        return "empty_population"
    if all(row.diagnostic for row in rows):
        return "diagnostic_only_population"
    if not usable:
        return "insufficient_repeats"
    if len(usable) < policy.minimum_cases:
        return "insufficient_cases"
    if policy.iterations < policy.minimum_iterations:
        return "insufficient_iterations"
    return None


def _detectability(result: PermutationEvidence, alpha: float) -> Detectability:
    if result.minimum_attainable_p is not None:
        return "vacuous" if result.minimum_attainable_p >= alpha else "detectable_exact"
    if result.resolution_floor is not None and result.resolution_floor >= alpha:
        return "vacuous"
    return "unresolved"


def _iteration(usable: tuple[AACase, ...], policy: AAPolicy, rng: random.Random) -> AAIteration:
    differences: list[float] = []
    for row in usable:
        shuffled = list(row.outcomes)
        rng.shuffle(shuffled)
        half = len(shuffled) // 2
        arm_a, arm_b = shuffled[:half], shuffled[half : 2 * half]
        differences.append(sum(arm_b) / half - sum(arm_a) / half)
    result = permutation_evidence(
        differences,
        seed=rng.getrandbits(64),
        iterations=policy.permutation_iterations,
        max_work=policy.max_work,
    )
    return AAIteration(
        cast(float, result.p_value),
        result.minimum_attainable_p,
        result.resolution_floor,
        _detectability(result, policy.significance_level),
        sum(value != 0 for value in differences),
        result.completed_iterations,
    )


def measure_aa(cases: Sequence[AACase], *, policy: AAPolicy) -> AAEvidence:
    """Regroup supplied Boolean repeats, preserving absence and threshold limits.

    Reservation is iterations*(usable repeats+n*T(n)), not actual CPU/time.
    It excludes validation, sorting, allocation, one-time preprocessing and
    bigint bit complexity. The full reservation is checked before any RNG.
    """
    _policy(policy)
    rows = _cases(cases)
    usable = tuple(row for row in rows if not row.diagnostic and len(row.outcomes) >= _TWO_ARMS)
    reason = _absence(rows, usable, policy)
    repeats = sum(len(row.outcomes) for row in usable)
    n = len(usable)
    transforms = 2**n if n <= EXACT_MAX_CASES else policy.permutation_iterations
    reserved = 0 if reason else policy.iterations * (repeats + n * transforms)
    if reserved > policy.max_work:
        raise AAError("COMPUTATION_LIMIT", f"reserved={reserved}, max_work={policy.max_work}")
    iterations: tuple[AAIteration, ...] = ()
    if not reason:
        rng = random.Random(policy.seed)
        iterations = tuple(_iteration(usable, policy, rng) for _ in range(policy.iterations))
    return AAEvidence(
        replace(policy),
        tuple(row.case_id for row in usable),
        tuple(row.case_id for row in rows if row.diagnostic),
        tuple(row.case_id for row in rows if not row.diagnostic and len(row.outcomes) < _TWO_ARMS),
        repeats,
        sum(len(row.outcomes) % 2 for row in usable),
        iterations,
        reserved,
        reason,
        (
            "conditional_random_halves_of_one_recorded_corpus",
            "not_independent_future_trials_or_population_calibration",
            "scoring_execution_and_exchangeability_not_verified",
            "source_sign_test_replaced_by_mean_signflip",
            "sorted_cases_and_derived_substreams_differ_from_source",
            "no_adequacy_acceptance_or_deployment_authority",
        ),
    )
