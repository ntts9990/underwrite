"""Standalone behavior and boundary checks."""

from __future__ import annotations

import itertools
import json
import math
import random
import sys
from dataclasses import FrozenInstanceError, asdict
from fractions import Fraction
from pathlib import Path
from typing import cast

import pytest
from _repo_paths import repo_root

from underwrite.instrument.evidence._resampling import (
    ResamplingError,
    ResamplingPolicy,
    percentile_interval,
    validate_policy,
)
from underwrite.instrument.evidence.paired import (
    CaseScore,
    PairedError,
    PairedEvidence,
    measure_paired,
    permutation_evidence,
)

BOOTSTRAP_DRAWS = 1000
PERMUTATION_DRAWS = 10000
CONFIDENCE = 0.95
MIN_UNCERTAINTY_POPULATION = 2


@pytest.fixture
def seed() -> int:
    schema = repo_root(Path(__file__).resolve()) / "contracts/statistical_seed.v1.schema.json"
    value: object = json.loads(schema.read_text())["const"]
    assert type(value) is int
    return value


def _policy(seed: int, iterations: int = 1000) -> ResamplingPolicy:
    return ResamplingPolicy(seed, iterations, 0.95)


def _arms(differences: list[int | float]) -> tuple[list[CaseScore], list[CaseScore]]:
    return (
        [CaseScore(f"case-{i:02d}", 0) for i in range(len(differences))],
        [CaseScore(f"case-{i:02d}", value) for i, value in enumerate(differences)],
    )


def _measure(
    differences: list[int | float],
    seed: int,
    *,
    max_work: int = 1000000,
) -> PairedEvidence:
    return measure_paired(
        *_arms(differences),
        resampling=_policy(seed),
        permutation_iterations=10000,
        max_work=max_work,
    )


def _quantile(samples: list[float], rank: float) -> float:
    lower = math.floor(rank)
    weight = Fraction.from_float(rank - lower)
    upper = min(lower + 1, len(samples) - 1)
    return float((1 - weight) * Fraction(samples[lower]) + weight * Fraction(samples[upper]))


@pytest.mark.parametrize(
    "values,expected",
    [
        ([1, 2], 0.5),
        ([0, 1], 1.0),
        ([1, -1], 1.0),
        ([0, 0], 1.0),
        ([1, 1, 1], 0.25),
        ([1.0, -1.0, math.ulp(0.0)], 1.0),
    ],
)
def test_exact_permutation_matches_fraction_enumeration(
    values: list[int | float],
    expected: float,
    seed: int,
) -> None:
    fractions = [Fraction(value) for value in values]
    observed = abs(sum(fractions))
    sums = [
        abs(sum(v * s for v, s in zip(fractions, signs, strict=True)))
        for signs in itertools.product((-1, 1), repeat=len(values))
    ]
    evidence = permutation_evidence(values, seed=seed, iterations=10000, max_work=100000)
    assert evidence.p_value == expected == sum(s >= observed for s in sums) / len(sums)
    assert evidence.minimum_attainable_p == sums.count(max(sums)) / len(sums)
    assert evidence.extreme_count == sum(s >= observed for s in sums)
    assert evidence.completed_iterations == 2 ** len(values)
    assert evidence.requested_iterations == PERMUTATION_DRAWS
    assert evidence.resolution_floor is None
    assert evidence.method == "mean-signflip-exact-v1"
    assert evidence.reason is None
    assert evidence.n == len(values)
    assert evidence.seed == seed


def test_empty_and_singleton_keep_absence_honest(seed: int) -> None:
    empty = _measure([], seed, max_work=1)
    assert (empty.n, empty.effect, empty.direction, empty.interval) == (0, None, None, None)
    assert empty.reason == "empty_population"
    assert empty.bootstrap_completed == empty.reserved_work == 0
    assert empty.permutation.p_value is None
    assert empty.permutation.completed_iterations == empty.permutation.extreme_count == 0
    assert empty.permutation.minimum_attainable_p is None
    assert empty.permutation.reason == "empty_population"
    one = _measure([2], seed, max_work=2)
    assert (one.effect, one.direction, one.permutation.p_value) == (2.0, "positive", 1.0)
    assert one.interval is None
    assert one.reason == "insufficient_resampling_population"
    assert one.bootstrap_completed == 0
    assert one.bootstrap_requested == BOOTSTRAP_DRAWS
    assert one.reserved_work == one.permutation.completed_iterations == 2**one.n


def test_alignment_and_outputs_are_owned_immutable_records(seed: int) -> None:
    baseline = [CaseScore("b", 100), CaseScore("a", 3)]
    candidate = [CaseScore("a", 5), CaseScore("b", 99)]
    result = measure_paired(
        baseline, candidate, resampling=_policy(seed), permutation_iterations=10000, max_work=2008
    )
    reverse_order = measure_paired(
        list(reversed(baseline)),
        list(reversed(candidate)),
        resampling=_policy(seed),
        permutation_iterations=10000,
        max_work=2008,
    )
    assert result == reverse_order
    assert result.case_ids == ("a", "b")
    assert result.differences == (2, -1)
    assert result.effect == 1 / 2
    baseline.clear()
    candidate.append(CaseScore("outside", 55))
    assert result.case_ids == ("a", "b")
    with pytest.raises(FrozenInstanceError):
        result.effect = 88  # pyright: ignore[reportAttributeAccessIssue]
    assert not {"qualification", "winner", "classification", "significant"} & asdict(result).keys()


@pytest.mark.parametrize("differences", [[], [2], [1, -3]])
def test_effect_method_is_explicit_frozen_serializable_metadata(
    differences: list[int | float], seed: int
) -> None:
    result = _measure(differences, seed)
    assert result.effect_method == "mean-paired-difference-v1"
    assert asdict(result)["effect_method"] == result.effect_method
    with pytest.raises(FrozenInstanceError):
        result.effect_method = "other"  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_duplicate_ids_reject_atomically(side: str, seed: int) -> None:
    baseline, candidate = _arms([1, 2])
    (baseline if side == "baseline" else candidate).append(CaseScore("case-00", 4))
    with pytest.raises(PairedError, match="DUPLICATE_CASE_ID.*case-00"):
        measure_paired(
            baseline,
            candidate,
            resampling=_policy(seed),
            permutation_iterations=10000,
            max_work=10000,
        )


def test_unmatched_ids_report_both_sides(seed: int) -> None:
    with pytest.raises(PairedError) as caught:
        measure_paired(
            [CaseScore("a", 0)],
            [CaseScore("b", 1)],
            resampling=_policy(seed),
            permutation_iterations=10000,
            max_work=10000,
        )
    assert caught.value.code == "UNMATCHED_CASE_IDS"
    assert caught.value.missing_baseline == ("b",)
    assert caught.value.missing_candidate == ("a",)


@pytest.mark.parametrize(
    "value,code",
    [
        (True, "INVALID_SCORE"),
        ("1", "INVALID_SCORE"),
        (None, "INVALID_SCORE"),
        (float("nan"), "UNREPRESENTABLE_SCORE"),
        (float("inf"), "UNREPRESENTABLE_SCORE"),
        (10**400, "UNREPRESENTABLE_SCORE"),
    ],
)
def test_invalid_numeric_difference_is_typed(value: object, code: str, seed: int) -> None:
    with pytest.raises(PairedError) as caught:
        permutation_evidence([cast(float, value)], seed=seed, iterations=10000, max_work=4)
    assert caught.value.code == code
    assert str(caught.value) == code


@pytest.mark.parametrize("identifier", ["", " ", "e\u0301", "\ud800", True, 4])
def test_invalid_case_ids_rejected(identifier: object, seed: int) -> None:
    with pytest.raises(PairedError, match="INVALID_CASE_ID") as caught:
        measure_paired(
            [CaseScore(cast(str, identifier), 0)],
            [],
            resampling=_policy(seed),
            permutation_iterations=10000,
            max_work=1,
        )
    assert caught.value.code == "INVALID_CASE_ID"
    assert str(caught.value) == "INVALID_CASE_ID: baseline"


@pytest.mark.parametrize("container", [{}, "abc", iter((1, 2))])
def test_non_concrete_populations_rejected(container: object, seed: int) -> None:
    with pytest.raises(PairedError, match="INVALID_POPULATION"):
        permutation_evidence(
            cast(list[int], container), seed=seed, iterations=10000, max_work=10000
        )


def test_record_types_and_numeric_subclasses_are_rejected(seed: int) -> None:
    class Number(int):
        pass

    with pytest.raises(PairedError, match="INVALID_SCORE"):
        _measure([Number(1)], seed)
    with pytest.raises(PairedError, match="INVALID_CASE_SCORE"):
        measure_paired(
            cast(list[CaseScore], [{"case_id": "a", "score": 0}]),
            [],
            resampling=_policy(seed),
            permutation_iterations=10000,
            max_work=1,
        )


def test_exact_integer_difference_and_explicit_mixed_rules(seed: int) -> None:
    def compare(a: int | float, b: int | float) -> PairedEvidence:
        return measure_paired(
            [CaseScore("a", a)],
            [CaseScore("a", b)],
            resampling=_policy(seed),
            permutation_iterations=10000,
            max_work=2,
        )

    assert compare(2**53, 2**53 + 1).effect == 1.0
    assert compare(2, 3.0).effect == 1.0
    assert compare(2.0, 3).effect == 1.0
    for a, b in [(2**53 + 1, float(2**53)), (float(2**53), 2**53 + 1)]:
        with pytest.raises(PairedError, match="UNREPRESENTABLE_MIXED_SCORE"):
            compare(a, b)
    for a, b in [(-1e308, 1e308), (-(10**308), 10**308)]:
        with pytest.raises(PairedError, match="NONFINITE_DIFFERENCE"):
            compare(a, b)
    with pytest.raises(PairedError, match="UNREPRESENTABLE_SCORE"):
        compare(10**400, 1)


def test_exact_tails_do_not_round_near_ties(seed: int) -> None:
    values = [1.0, math.ulp(0.0)]
    result = permutation_evidence(values, seed=seed, iterations=10000, max_work=8)
    assert result.p_value == 1 / 2
    assert result.minimum_attainable_p == 1 / 2


def test_mc_uses_actual_uniform_draws_and_plus_one(seed: int) -> None:
    values = list(range(1, 14))
    rng = random.Random(seed)
    extreme = sum(
        abs(sum(v if rng.getrandbits(1) else -v for v in values)) >= sum(values)
        for _ in range(10000)
    )
    result = permutation_evidence(values, seed=seed, iterations=10000, max_work=130000)
    assert result.method == "mean-signflip-monte-carlo-v1"
    assert result.extreme_count == extreme
    assert result.p_value == (extreme + 1) / 10001
    assert result.completed_iterations == result.requested_iterations == PERMUTATION_DRAWS
    assert result.minimum_attainable_p is None
    assert result.resolution_floor == 1 / 10001
    assert result.reserved_work == len(values) * PERMUTATION_DRAWS


def test_exact_cutoff_counts_zero_pairs_and_mc_zero_minimum(seed: int) -> None:
    exact = permutation_evidence([0] * 12, seed=seed, iterations=10000, max_work=49152)
    assert exact.completed_iterations == 2**12
    assert exact.p_value == exact.minimum_attainable_p == 1.0
    mc = permutation_evidence([0] * 13, seed=seed, iterations=10000, max_work=130000)
    assert mc.completed_iterations == PERMUTATION_DRAWS
    assert mc.p_value == mc.minimum_attainable_p == 1.0


@pytest.mark.parametrize("n,reservation", [(0, 0), (1, 2), (2, 2008), (12, 61152), (13, 143000)])
def test_branch_reservations(n: int, reservation: int, seed: int) -> None:
    result = _measure([1] * n, seed, max_work=max(1, reservation))
    assert result.reserved_work == reservation
    assert result.max_work == max(1, reservation)
    if reservation:
        with pytest.raises(PairedError, match="COMPUTATION_LIMIT"):
            _measure([1] * n, seed, max_work=reservation - 1)


def test_limit_rejection_precedes_rng(monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("sampling occurred before reservation validation")

    monkeypatch.setattr("underwrite.instrument.evidence.paired.random.Random", forbidden)
    with pytest.raises(PairedError, match="COMPUTATION_LIMIT"):
        _measure([1, 2], seed, max_work=2007)
    with pytest.raises(PairedError, match="COMPUTATION_LIMIT"):
        permutation_evidence([1] * 13, seed=seed, iterations=10000, max_work=129999)


def test_bootstrap_draws_remain_paired_and_use_independent_rank_oracle(seed: int) -> None:
    baseline = [CaseScore("a", 100), CaseScore("b", -200), CaseScore("c", 300)]
    candidate = [CaseScore("a", 101), CaseScore("b", -197), CaseScore("c", 298)]
    differences = [1, 3, -2]
    rng = random.Random(seed)
    samples = sorted(
        float(Fraction(sum(differences[rng.randrange(3)] for _ in range(3)), 3))
        for _ in range(1000)
    )
    low = 999 * ((1.0 - 0.95) / 2.0)
    expected = (_quantile(samples, low), _quantile(samples, 999.0 - low))
    result = measure_paired(
        baseline, candidate, resampling=_policy(seed), permutation_iterations=10000, max_work=3024
    )
    assert result.effect == float(Fraction(2, 3))
    assert result.interval == expected
    assert result.bootstrap_completed == result.bootstrap_requested == BOOTSTRAP_DRAWS
    assert result.confidence == CONFIDENCE
    assert result.bootstrap_method == "percentile-linear-v1"


@pytest.mark.parametrize("values", [[1e308, 1e308], [math.ulp(0.0), math.ulp(0.0)], [1, 3, -2]])
def test_extremes_and_reflection(values: list[int | float], seed: int) -> None:
    result = _measure(values, seed)
    reflected = _measure([-v for v in values], seed)
    assert result.effect == -cast(float, reflected.effect)
    assert result.permutation.p_value == reflected.permutation.p_value
    assert result.interval is not None and reflected.interval is not None
    scale = max(abs(v) for v in values)
    tolerance = max(8 * math.ulp(0.0), (16 * sys.float_info.epsilon * 1000) * scale)
    assert result.interval == pytest.approx(
        tuple(-v for v in reversed(reflected.interval)), rel=0, abs=tolerance
    )
    assert all(math.isfinite(value) for value in result.interval)


def test_constant_and_zero_are_not_certainty(seed: int) -> None:
    for value, direction in [(0, "zero"), (-3, "negative"), (2, "positive")]:
        result = _measure([value, value], seed)
        assert result.effect == value
        assert result.direction == direction
        assert result.interval == (float(value), float(value))
        assert "degenerate_empirical_distribution" in result.limitations
        assert result.reason is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", True),
        ("seed", 42.0),
        ("bootstrap_iterations", 999),
        ("bootstrap_iterations", 10001),
        ("bootstrap_iterations", True),
        ("bootstrap_iterations", 1000.0),
        ("confidence", 1),
        ("confidence", 0.0),
        ("confidence", 1.0),
        ("confidence", float("nan")),
        ("confidence", float("inf")),
        ("confidence", -float("inf")),
        ("confidence", -0.5),
        ("confidence", True),
        ("confidence", "0.95"),
    ],
)
def test_invalid_resampling_policy(field: str, value: object, seed: int) -> None:
    arguments: dict[str, object] = {"seed": seed, "bootstrap_iterations": 1000, "confidence": 0.95}
    arguments[field] = value
    policy = ResamplingPolicy(
        cast(int, arguments["seed"]),
        cast(int, arguments["bootstrap_iterations"]),
        cast(float, arguments["confidence"]),
    )
    with pytest.raises(ResamplingError):
        validate_policy(policy)


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", True),
        ("seed", 1.0),
        ("iterations", 9999),
        ("iterations", True),
        ("iterations", 10000.0),
        ("max_work", 0),
        ("max_work", True),
        ("max_work", 1.0),
    ],
)
def test_invalid_permutation_settings(field: str, value: object, seed: int) -> None:
    arguments: dict[str, object] = {"seed": seed, "iterations": 10000, "max_work": 10000}
    arguments[field] = value
    with pytest.raises(PairedError) as caught:
        permutation_evidence(
            [],
            seed=cast(int, arguments["seed"]),
            iterations=cast(int, arguments["iterations"]),
            max_work=cast(int, arguments["max_work"]),
        )
    expected = {
        "seed": "INVALID_SEED",
        "iterations": "INVALID_PERMUTATION_ITERATIONS",
        "max_work": "INVALID_MAX_WORK",
    }[field]
    assert caught.value.code == str(caught.value) == expected


@pytest.mark.parametrize("differences", [[], [2], [1, -3], [0, 0]])
def test_all_populations_retain_method_scope_and_caller_policy(
    differences: list[int | float], seed: int
) -> None:
    policy = ResamplingPolicy(seed + 1, BOOTSTRAP_DRAWS, 0.8)
    budget = 20000
    result = measure_paired(
        *_arms(differences),
        resampling=policy,
        permutation_iterations=PERMUTATION_DRAWS,
        max_work=budget,
    )
    expected = {
        "joint_sign_invariance_under_null_required",
        "independent_sampling_units_required",
        "sampling_representativeness_not_established",
        "optional_stopping_not_accounted",
        "descriptive_statistics_only",
    }
    if len(differences) >= MIN_UNCERTAINTY_POPULATION and len(set(differences)) == 1:
        expected.add("degenerate_empirical_distribution")
    assert set(result.limitations) == expected
    assert len(result.limitations) == len(expected)
    assert result.seed == result.permutation.seed == policy.seed
    assert result.confidence == policy.confidence
    assert result.bootstrap_method == "percentile-linear-v1"
    assert result.bootstrap_requested == policy.bootstrap_iterations
    assert result.permutation.max_work == result.max_work == budget
    assert result.permutation.n == result.n == len(differences)
    assert result.permutation.requested_iterations == PERMUTATION_DRAWS
    assert result.permutation.method == "mean-signflip-exact-v1"
    assert result.permutation.resolution_floor is None
    assert result.permutation.reserved_work == len(differences) * (
        2 ** len(differences) if differences else 0
    )


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize(
    "malformed,code", [(None, "INVALID_POPULATION"), ([None], "INVALID_CASE_SCORE")]
)
def test_malformed_arm_reports_exact_side(
    side: str, malformed: object, code: str, seed: int
) -> None:
    baseline, candidate = _arms([])
    if side == "baseline":
        baseline = cast(list[CaseScore], malformed)
    else:
        candidate = cast(list[CaseScore], malformed)
    with pytest.raises(PairedError) as caught:
        measure_paired(
            baseline,
            candidate,
            resampling=_policy(seed),
            permutation_iterations=PERMUTATION_DRAWS,
            max_work=1,
        )
    assert caught.value.code == code
    assert str(caught.value) == f"{code}: {side}"


def test_work_error_retains_required_and_allowed_budget(seed: int) -> None:
    differences: list[int | float] = [1, -2, 3]
    required = len(differences) * (2 ** len(differences) + BOOTSTRAP_DRAWS)
    budget = required - 1
    with pytest.raises(PairedError) as caught:
        _measure(differences, seed, max_work=budget)
    assert caught.value.code == "COMPUTATION_LIMIT"
    assert str(caught.value) == f"COMPUTATION_LIMIT: reserved={required}, max_work={budget}"


@pytest.mark.parametrize(
    "confidence", [0.8, 0.95, math.nextafter(0.0, 1.0), math.nextafter(1.0, 0.0)]
)
def test_percentiles_follow_explicit_binary_rank_recipe(confidence: float) -> None:
    values = [float(i) for i in range(1000)]
    low = 999 * ((1.0 - confidence) / 2.0)
    assert percentile_interval(values, confidence=confidence) == (
        _quantile(values, low),
        _quantile(values, 999.0 - low),
    )


def test_linear_percentile_intentionally_differs_from_source_discrete_convention() -> None:
    values = [float(i) for i in range(1000)]
    interval = percentile_interval(values, confidence=0.95)
    assert interval == pytest.approx((24.975, 974.025))
    assert interval != (values[25], values[975])
    assert percentile_interval([-1e308, 1e308], confidence=0.95) == (-9.5e307, 9.5e307)


def test_bootstrap_maximum_and_diagnostic_seed_are_executed(seed: int) -> None:
    result = measure_paired(
        *_arms([1, 3]),
        resampling=_policy(seed + 7, 10000),
        permutation_iterations=10000,
        max_work=20008,
    )
    assert result.seed == seed + 7
    assert result.bootstrap_completed == BOOTSTRAP_DRAWS * 10
    assert result.interval == (1.0, 3.0)


@pytest.mark.parametrize("samples", [[], {}, [1], [True], [float("nan")], [float("inf")]])
def test_percentile_rejects_invalid_runtime_samples(samples: object) -> None:
    with pytest.raises(ResamplingError):
        percentile_interval(cast(list[float], samples), confidence=CONFIDENCE)


def test_percentile_is_nonmutating_and_singleton_is_only_a_helper_value() -> None:
    values = [4.0, -2.0, 3.0]
    before = values.copy()
    percentile_interval(values, confidence=CONFIDENCE)
    assert values == before
    assert percentile_interval((3.0,), confidence=CONFIDENCE) == (3.0, 3.0)
    # Paired's public n1 policy separately withholds an uncertainty interval.


def test_frozen_policy_construction_does_not_bypass_exact_record_validation() -> None:
    class DerivedPolicy(ResamplingPolicy):
        pass

    for policy in (object(), DerivedPolicy(7, BOOTSTRAP_DRAWS, CONFIDENCE)):
        with pytest.raises(ResamplingError, match="INVALID_RESAMPLING_POLICY"):
            validate_policy(cast(ResamplingPolicy, policy))


def test_float_subclass_cannot_bypass_confidence_boundary(seed: int) -> None:
    class FloatLike(float):
        pass

    confidence = FloatLike(CONFIDENCE)
    with pytest.raises(ResamplingError, match="INVALID_CONFIDENCE"):
        validate_policy(ResamplingPolicy(seed, BOOTSTRAP_DRAWS, confidence))
    with pytest.raises(ResamplingError, match="INVALID_CONFIDENCE"):
        percentile_interval([1.0, 2.0], confidence=confidence)


def test_mean_and_linear_interval_match_independent_resampling(seed: int) -> None:
    values: list[int | float] = list(range(12))
    result = _measure(values, seed)
    expected_mean = float(sum(Fraction(value) for value in values) / len(values))
    assert result.effect == expected_mean
    rng = random.Random(seed)
    samples = sorted(
        sum(values[rng.randrange(len(values))] for _ in values) / len(values)
        for _ in range(BOOTSTRAP_DRAWS)
    )
    low = (BOOTSTRAP_DRAWS - 1) * ((1.0 - CONFIDENCE) / 2.0)
    assert result.interval == (
        _quantile(samples, low),
        _quantile(samples, float(BOOTSTRAP_DRAWS - 1) - low),
    )
    # Protect the different conventions without making a changed canonical
    # seed fail on a historical numeric CI snapshot.
    assert result.bootstrap_method == "percentile-linear-v1"


def test_explicit_power_of_two_scaling_preserves_exact_tail(seed: int) -> None:
    first = permutation_evidence(
        [0.25, -0.5, 0.125], seed=seed, iterations=PERMUTATION_DRAWS, max_work=24
    )
    scaled = permutation_evidence([2, -4, 1], seed=seed, iterations=PERMUTATION_DRAWS, max_work=24)
    assert first.p_value == scaled.p_value
    assert first.minimum_attainable_p == scaled.minimum_attainable_p
