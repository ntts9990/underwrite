"""Compute paired sign-flip evidence with explicit resampling parameters.

Callers supply the seed and observations. Numerical evidence is not a deployment
verdict, and insufficient paired data stays explicitly unmeasured."""

from __future__ import annotations

import itertools
import math
import random
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

from underwrite.instrument.evidence._resampling import (
    ResamplingPolicy,
    percentile_interval,
    validate_policy,
)

EXACT_MAX_CASES = 12
MIN_PERMUTATION_ITERATIONS = 10000
MIN_BOOTSTRAP_CASES = 2


class PairedError(ValueError):
    """Atomic invalid input or an exceeded reserved traversal proxy."""

    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        missing_baseline: tuple[str, ...] = (),
        missing_candidate: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.missing_baseline = missing_baseline
        self.missing_candidate = missing_candidate
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class CaseScore:
    """A supplied score for one named sampling unit, not verified execution."""

    case_id: str
    score: int | float


@dataclass(frozen=True)
class PermutationEvidence:
    """Inclusive exact tails or plus-one MC tails; no bootstrap or decision."""

    n: int
    seed: int
    method: str
    p_value: float | None
    requested_iterations: int
    completed_iterations: int
    extreme_count: int
    minimum_attainable_p: float | None
    resolution_floor: float | None
    reserved_work: int
    max_work: int
    reason: str | None


@dataclass(frozen=True)
class PairedEvidence:
    """Owned immutable observations; direction is candidate minus baseline."""

    case_ids: tuple[str, ...]
    differences: tuple[int | float, ...]
    effect: float | None
    direction: Literal["negative", "zero", "positive"] | None
    interval: tuple[float, float] | None
    reason: str | None
    permutation: PermutationEvidence
    seed: int
    bootstrap_requested: int
    bootstrap_completed: int
    confidence: float
    bootstrap_method: str
    reserved_work: int
    max_work: int
    limitations: tuple[str, ...]
    effect_method: Literal["mean-paired-difference-v1"] = field(
        default="mean-paired-difference-v1", init=False
    )

    @property
    def n(self) -> int:
        return len(self.case_ids)


def _number(value: int | float, code: str = "UNREPRESENTABLE_SCORE") -> None:
    if type(value) not in (int, float):
        raise PairedError("INVALID_SCORE")
    try:
        finite = math.isfinite(float(value))
    except OverflowError as exc:
        raise PairedError(code) from exc
    if not finite:
        raise PairedError(code)


def _settings(seed: int, iterations: int, max_work: int) -> None:
    if type(seed) is not int:
        raise PairedError("INVALID_SEED")
    if type(iterations) is not int or iterations < MIN_PERMUTATION_ITERATIONS:
        raise PairedError("INVALID_PERMUTATION_ITERATIONS")
    if type(max_work) is not int or max_work <= 0:
        raise PairedError("INVALID_MAX_WORK")


def _transforms(n: int, iterations: int) -> int:
    if not n:
        return 0
    return 2**n if n <= EXACT_MAX_CASES else iterations


def _reserve(work: int, max_work: int) -> None:
    if work > max_work:
        raise PairedError("COMPUTATION_LIMIT", f"reserved={work}, max_work={max_work}")


def _scaled(values: tuple[int | float, ...]) -> tuple[tuple[int, ...], int]:
    ratios = tuple(value.as_integer_ratio() for value in values)
    denominator = max(pair[1] for pair in ratios)
    return tuple(numerator * (denominator // scale) for numerator, scale in ratios), denominator


def _mean(total: int, denominator: int) -> float:
    try:
        result = float(Fraction(total, denominator))
    except OverflowError as exc:
        raise PairedError("NONFINITE_STATISTIC") from exc
    if not math.isfinite(result):
        raise PairedError("NONFINITE_STATISTIC")
    return result


def _tails(values: tuple[int, ...], seed: int, iterations: int) -> tuple[int, float | None]:
    observed = abs(sum(values))
    if len(values) <= EXACT_MAX_CASES:
        statistics = [
            abs(sum(value * sign for value, sign in zip(values, signs, strict=True)))
            for signs in itertools.product((-1, 1), repeat=len(values))
        ]
        maximum = max(statistics)
        return (
            sum(statistic >= observed for statistic in statistics),
            statistics.count(maximum) / len(statistics),
        )
    rng = random.Random(seed)
    extreme = sum(
        abs(sum(value if rng.getrandbits(1) else -value for value in values)) >= observed
        for _ in range(iterations)
    )
    return extreme, 1.0 if not any(values) else None


def permutation_evidence(
    differences: Sequence[int | float],
    *,
    seed: int,
    iterations: int,
    max_work: int,
) -> PermutationEvidence:
    """Compare exact binary absolute sums, including all zero multiplicities.

    ``reserved_work=n*T(n)`` counts sign traversals, not validation, sorting,
    allocation, preprocessing or bigint bit complexity; it is not a time quota.
    Exact support minimum uses all transforms, never sampled MC extrema.
    """
    _settings(seed, iterations, max_work)
    if type(differences) not in (list, tuple):
        raise PairedError("INVALID_POPULATION")
    for value in differences:
        _number(value)
    owned = tuple(differences)
    n = len(owned)
    performed = _transforms(n, iterations)
    reserved = n * performed
    _reserve(reserved, max_work)
    exact = n <= EXACT_MAX_CASES
    method = "mean-signflip-exact-v1" if exact else "mean-signflip-monte-carlo-v1"
    if not n:
        return PermutationEvidence(
            n,
            seed,
            method,
            None,
            iterations,
            0,
            0,
            None,
            None,
            reserved,
            max_work,
            "empty_population",
        )
    scaled, _ = _scaled(owned)
    extreme, minimum = _tails(scaled, seed, iterations)
    p_value = extreme / performed if exact else (extreme + 1) / (performed + 1)
    return PermutationEvidence(
        n,
        seed,
        method,
        p_value,
        iterations,
        performed,
        extreme,
        minimum,
        None if exact else 1 / (iterations + 1),
        reserved,
        max_work,
        None,
    )


def _arm(scores: list[CaseScore] | tuple[CaseScore, ...], side: str) -> dict[str, int | float]:
    if type(scores) not in (list, tuple):
        raise PairedError("INVALID_POPULATION", side)
    result: dict[str, int | float] = {}
    for item in scores:
        if type(item) is not CaseScore:
            raise PairedError("INVALID_CASE_SCORE", side)
        case_id = item.case_id
        if (
            type(case_id) is not str
            or not case_id.strip()
            or not unicodedata.is_normalized("NFC", case_id)
        ):
            raise PairedError("INVALID_CASE_ID", side)
        try:
            case_id.encode("utf-8")
        except UnicodeError as exc:
            raise PairedError("INVALID_CASE_ID", side) from exc
        _number(item.score)
        if case_id in result:
            raise PairedError("DUPLICATE_CASE_ID", f"{side}:{case_id}")
        result[case_id] = item.score
    return result


def _difference(baseline: int | float, candidate: int | float) -> int | float:
    if type(baseline) is int and type(candidate) is int:
        value = candidate - baseline
    else:
        for score in (baseline, candidate):
            if type(score) is int and int(float(score)) != score:
                raise PairedError("UNREPRESENTABLE_MIXED_SCORE")
        value = float(candidate) - float(baseline)
    _number(value, "NONFINITE_DIFFERENCE")
    return value


def _bootstrap(
    differences: tuple[int | float, ...],
    policy: ResamplingPolicy,
) -> tuple[float, float]:
    values, denominator = _scaled(differences)
    n = len(values)
    rng = random.Random(policy.seed)
    samples = [
        _mean(sum(values[rng.randrange(n)] for _ in range(n)), denominator * n)
        for _ in range(policy.bootstrap_iterations)
    ]
    return percentile_interval(samples, confidence=policy.confidence)


def measure_paired(
    baseline: list[CaseScore] | tuple[CaseScore, ...],
    candidate: list[CaseScore] | tuple[CaseScore, ...],
    *,
    resampling: ResamplingPolicy,
    permutation_iterations: int,
    max_work: int,
) -> PairedEvidence:
    """Align whole populations before any RNG; absent pairs are never dropped.

    Mean and percentile interval do not invert the sign-flip test. The combined
    reserved traversal proxy includes bootstrap only for n>=2, plus permutation.
    Each procedure has its own local RNG from the supplied seed for replay, not
    statistical independence. Direction follows the exact difference total;
    a final float mean can round an extremely small nonzero value to zero.
    """
    validate_policy(resampling)
    _settings(resampling.seed, permutation_iterations, max_work)
    left, right = _arm(baseline, "baseline"), _arm(candidate, "candidate")
    if left.keys() != right.keys():
        missing_left, missing_right = (
            tuple(sorted(right.keys() - left.keys())),
            tuple(sorted(left.keys() - right.keys())),
        )
        raise PairedError(
            "UNMATCHED_CASE_IDS",
            f"baseline={missing_left}, candidate={missing_right}",
            missing_baseline=missing_left,
            missing_candidate=missing_right,
        )
    case_ids = tuple(sorted(left))
    differences = tuple(_difference(left[key], right[key]) for key in case_ids)
    n = len(case_ids)
    completed = resampling.bootstrap_iterations if n >= MIN_BOOTSTRAP_CASES else 0
    reserved = n * (_transforms(n, permutation_iterations) + completed)
    _reserve(reserved, max_work)
    permutation = permutation_evidence(
        differences, seed=resampling.seed, iterations=permutation_iterations, max_work=max_work
    )
    effect: float | None = None
    direction: Literal["negative", "zero", "positive"] | None = None
    interval: tuple[float, float] | None = None
    limitations = (
        "joint_sign_invariance_under_null_required",
        "independent_sampling_units_required",
        "sampling_representativeness_not_established",
        "optional_stopping_not_accounted",
        "descriptive_statistics_only",
    )
    reason: str | None = "empty_population" if not n else "insufficient_resampling_population"
    if n:
        values, denominator = _scaled(differences)
        total = sum(values)
        effect = _mean(total, denominator * n)
        direction = "positive" if total > 0 else "negative" if total < 0 else "zero"
    if completed:
        interval = _bootstrap(differences, resampling)
        reason = None
        if len(set(differences)) == 1:
            limitations += ("degenerate_empirical_distribution",)
    return PairedEvidence(
        case_ids,
        differences,
        effect,
        direction,
        interval,
        reason,
        permutation,
        resampling.seed,
        resampling.bootstrap_iterations,
        completed,
        resampling.confidence,
        "percentile-linear-v1",
        reserved,
        max_work,
        limitations,
    )
