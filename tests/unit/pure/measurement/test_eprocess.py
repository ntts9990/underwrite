"""Standalone behavior and boundary checks."""

from __future__ import annotations

import itertools
import json
import math
import sys
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from hypothesis import given, settings
from hypothesis import strategies as st

from underwrite.measurement import eprocess
from underwrite.measurement.eprocess import (
    CONTRACT_P0,
    CONTRACT_PASS_E,
    CONTRACT_PRIOR_A,
    CONTRACT_PRIOR_B,
    METHOD,
    STOP_REASONS,
    WEALTH_ABSORBED,
    EProcessError,
    EProcessPolicy,
    EProcessRun,
    Observation,
    binary_observations,
    check_observation,
    check_policy,
    check_projection,
    parse_projection,
    project,
    projection_of,
    run_stream,
    score,
    verdict,
    wealths,
)

ROOT = repo_root(Path(__file__))

SCHEMA = ROOT / "contracts/read.v1.schema.json"
NULL_ID = "binary:p=0.5"
POLICY = EProcessPolicy(
    CONTRACT_P0, CONTRACT_PRIOR_A, CONTRACT_PRIOR_B, CONTRACT_PASS_E, None, None, NULL_ID
)
_EXACT_VECTOR = {
    "stopped_sequence": 8,
    "observations_total": 10,
    "observations_used": 8,
    "trials_used": 8,
    "stop_reason": "threshold",
    "cost_used": 0.0,
    "e_value": float(Fraction(256, 9)),
    "verdict": "pass",
    "score": round(1 - 9 / 256, 6),
}
PARITY = 1e-9
CROSSING_STEP = int(_EXACT_VECTOR["stopped_sequence"])  # the exact run's first crossing
STREAM_LENGTH = int(_EXACT_VECTOR["observations_total"])
BUDGET = 3
SEPARATION = 1e-3


def test_consumed_cost_overflow_is_rejected() -> None:
    records = [Observation(1, 1, sys.float_info.max), Observation(0, 1, sys.float_info.max)]
    before = records.copy()
    with pytest.raises(EProcessError) as caught:
        run_stream(records, POLICY)
    assert caught.value.code == "COST_OVERFLOW"
    assert records == before


@pytest.mark.parametrize("total", [math.nextafter(sys.float_info.max, 0), sys.float_info.max])
def test_representable_cost_sum_at_float_limit(total: float) -> None:
    records = [Observation(1, 1, total / 2), Observation(0, 1, total / 2)]
    result = run_stream(records, POLICY)
    assert result.cost_used == total
    assert result.observations_used == len(records)
    check_projection(projection_of(result))
    exact_budget = run_stream(records, replace(POLICY, max_cost=total))
    assert exact_budget == replace(result, policy=replace(POLICY, max_cost=total))


@pytest.mark.parametrize("observation_limit", [None, 1])
def test_budget_precedes_prospective_cost_overflow(observation_limit: int | None) -> None:
    records = [Observation(1, 1, sys.float_info.max)] * 3
    result = run_stream(
        records, replace(POLICY, max_cost=sys.float_info.max, max_observations=observation_limit)
    )
    assert result.stop_reason == (
        "budget_cost" if observation_limit is None else "budget_observations"
    )
    assert result.cost_used == sys.float_info.max
    assert result.observations_used == 1
    check_projection(projection_of(result))


def test_threshold_leaves_trailing_large_cost_unconsumed() -> None:
    records = [Observation(1, 1, sys.float_info.max)] * 2
    result = run_stream(records, replace(POLICY, pass_e=1))
    assert result.stop_reason == "threshold"
    assert result.observations_used == 1
    assert result.cost_used == sys.float_info.max


def test_observation_limit_leaves_unbudgeted_large_cost_unconsumed() -> None:
    records = [Observation(1, 1, sys.float_info.max)] * 2
    result = run_stream(records, replace(POLICY, max_observations=1))
    assert result.stop_reason == "budget_observations"
    assert result.observations_used == 1
    assert result.cost_used == sys.float_info.max


@pytest.mark.parametrize("value", [10**400, -(10**400), True, math.nan, math.inf, -math.inf, "1"])
def test_unrepresentable_cost_keeps_field_error(value: object) -> None:
    with pytest.raises(EProcessError) as caught:
        run_stream([Observation(1, 1, cast("float", value))], POLICY)
    assert caught.value.code == "COST_NEGATIVE"


@pytest.mark.parametrize("value", [10**400, -(10**400), True, math.nan, math.inf, -math.inf, "1"])
@pytest.mark.parametrize(
    "field,code",
    [
        ("p0", "P0_OUT_OF_RANGE"),
        ("pass_e", "PASS_E_BELOW_ONE"),
        ("max_cost", "MAX_COST_NOT_POSITIVE"),
    ],
)
def test_unrepresentable_policy_number_keeps_field_error(
    value: object, field: str, code: str
) -> None:
    with pytest.raises(EProcessError) as caught:
        run_stream([Observation(1, 1, 0)], replace(POLICY, **{field: value}))
    assert caught.value.code == code


@pytest.mark.parametrize("value", [10**400, -(10**400), True, math.nan, math.inf, -math.inf, "1"])
@pytest.mark.parametrize(
    "field,code",
    [
        (field, "NUMBER_NOT_NONNEGATIVE_FINITE")
        for field in ("e_value", "e_upper", "e_lower", "cost_used")
    ]
    + [("pass_e", "PASS_E_BELOW_ONE"), ("max_cost", "MAX_COST_NOT_POSITIVE")],
)
def test_unrepresentable_projection_number_keeps_field_error(
    value: object, field: str, code: str
) -> None:
    projection = projection_of(run_stream([Observation(1, 1, 0)], POLICY))
    with pytest.raises(EProcessError) as caught:
        check_projection(replace(projection, **{field: value}))
    assert caught.value.code == code


@pytest.mark.parametrize(
    "value",
    [
        int(math.nextafter(sys.float_info.max, 0)),
        int(sys.float_info.max),
        math.nextafter(sys.float_info.max, 0),
        sys.float_info.max,
    ],
)
def test_representable_integer_and_float_cost_boundary(value: float) -> None:
    policy = replace(POLICY, max_cost=value, pass_e=value)
    result = run_stream([Observation(1, 1, value)], policy)
    assert result.cost_used == value
    projection = projection_of(result)
    assert check_projection(projection) == projection
    assert check_projection(replace(projection, cost_used=value)).cost_used == value


# --- exact oracle: binomial-tail form of the truncated Beta(1,1) integrals (independent of
# the alternating expansion the gates test uses) ---


def _tail(lower: int, upper: int, count: int, p0: Fraction) -> Fraction:
    return sum(
        (math.comb(count, j) * p0**j * (1 - p0) ** (count - j) for j in range(lower, upper + 1)),
        Fraction(0),
    )


def _exact_sides(s: int, f: int, p0: Fraction) -> tuple[Fraction, Fraction]:
    n = s + f
    beta = Fraction(math.factorial(s) * math.factorial(f), math.factorial(n + 1))
    likelihood = p0**s * (1 - p0) ** f
    lower = beta * _tail(s + 1, n + 1, n + 1, p0) / p0 / likelihood
    upper = beta * _tail(0, s, n + 1, p0) / (1 - p0) / likelihood
    return upper, lower


def _exact_full(s: int, f: int, p0: Fraction) -> Fraction:
    n = s + f
    beta = Fraction(math.factorial(s) * math.factorial(f), math.factorial(n + 1))
    return beta / (p0**s * (1 - p0) ** f)


def _relative(actual: float, expected: Fraction) -> Fraction:
    return abs(Fraction(actual) - expected) / expected


def _stream(outcomes: list[int], **overrides: Any) -> EProcessRun:
    return run_stream(binary_observations(outcomes, None), replace(POLICY, **overrides))


def _assert_refused(code: str, detail: str, call: Callable[[], object]) -> None:
    """The refusal's code and its full message (code plus the offending input) are the contract."""
    with pytest.raises(EProcessError) as caught:
        call()
    assert (caught.value.code, str(caught.value)) == (code, f"{code}: {detail}")


def _schema_block() -> dict[str, Any]:
    schema = cast("dict[str, Any]", json.loads(SCHEMA.read_text(encoding="utf-8")))
    return cast("dict[str, Any]", cast("dict[str, Any]", schema["$defs"])["eprocess"])


def test_the_exact_fixture_replays_with_the_same_e_value_stop_and_counts() -> None:
    fixture = _EXACT_VECTOR
    run = _stream([1] * STREAM_LENGTH)
    assert run.stop_reason == fixture["stop_reason"] == "threshold"
    assert run.observations_used == fixture["observations_used"] == CROSSING_STEP
    assert run.observations_total == fixture["observations_total"] == STREAM_LENGTH
    assert run.stopped_sequence == fixture["stopped_sequence"] == CROSSING_STEP
    assert run.trials == fixture["trials_used"] == CROSSING_STEP
    assert run.cost_used == fixture["cost_used"] == 0.0
    assert abs(run.e_value - float(fixture["e_value"])) / run.e_value <= PARITY
    assert _relative(run.e_value, Fraction(256, 9)) <= PARITY
    projection = projection_of(run)
    assert verdict(projection) == fixture["verdict"] == "pass"
    assert score(projection) == fixture["score"] == round(1 - 9 / 256, 6)


def test_uniform_closed_form_example_one_of_two() -> None:
    run = run_stream((Observation(1, 2, None),), POLICY)
    assert _relative(run.e_value, Fraction(2, 3)) <= PARITY
    assert run.stop_reason == "complete"
    assert verdict(projection_of(run)) == "warn"


@pytest.mark.parametrize("n", range(1, 13))
def test_each_side_matches_the_exact_oracle_and_their_mixture_is_uniforms_full_mixture(
    n: int,
) -> None:
    half = Fraction(1, 2)
    for s in range(n + 1):
        e_upper, e_lower, e_value = wealths(POLICY, s, n - s)
        exact_upper, exact_lower = _exact_sides(s, n - s, half)
        assert _relative(e_upper, exact_upper) <= PARITY
        assert _relative(e_lower, exact_lower) <= PARITY
        assert e_value == (e_upper + e_lower) / 2.0
        assert _relative(e_value, _exact_full(s, n - s, half)) <= PARITY


@pytest.mark.parametrize("n", range(1, 9))
def test_each_one_sided_wealth_and_the_mixture_have_null_expectation_one(n: int) -> None:
    weight = 0.5**n
    upper = lower = mixture = 0.0
    for s in range(n + 1):
        e_upper, e_lower, e_value = wealths(POLICY, s, n - s)
        probability = math.comb(n, s) * weight
        upper += probability * e_upper
        lower += probability * e_lower
        mixture += probability * e_value
    assert abs(upper - 1.0) <= PARITY
    assert abs(lower - 1.0) <= PARITY
    assert abs(mixture - 1.0) <= PARITY


@pytest.mark.parametrize(("s", "f", "p0"), [(100, 100, 2), (130, 70, 2), (60, 140, 4)])
def test_large_counts_stay_within_the_parity_tolerance_of_the_exact_tails(
    s: int, f: int, p0: int
) -> None:
    exact_upper, exact_lower = _exact_sides(s, f, Fraction(1, p0))
    e_upper, e_lower, e_value = wealths(replace(POLICY, p0=1 / p0), s, f)
    assert _relative(e_upper, exact_upper) <= PARITY
    assert _relative(e_lower, exact_lower) <= PARITY
    assert e_value == (e_upper + e_lower) / 2.0


def test_off_centre_p0_separates_the_equal_mixture_from_the_full_mixture() -> None:
    quarter = Fraction(1, 4)
    policy = replace(POLICY, p0=0.25)
    e_upper, e_lower, e_value = wealths(policy, 3, 1)
    exact_upper, exact_lower = _exact_sides(3, 1, quarter)
    assert _relative(e_upper, exact_upper) <= PARITY
    assert _relative(e_lower, exact_lower) <= PARITY
    assert _relative(e_value, (exact_upper + exact_lower) / 2) <= PARITY
    assert _relative(e_value, _exact_full(3, 1, quarter)) > SEPARATION


def test_integer_priors_other_than_one_keep_the_same_binomial_tail_identity() -> None:
    policy = replace(POLICY, prior_a=2, prior_b=3)
    e_upper, e_lower, e_value = wealths(policy, 4, 2)
    half = Fraction(1, 2)
    # Beta(2,3) restricted sides via the tail form: prior mass and data tails share one p0.
    a, b, s, f = 2, 3, 4, 2
    beta_ratio = Fraction(
        math.factorial(a + s - 1) * math.factorial(b + f - 1) * math.factorial(a + b - 1),
        math.factorial(a + b + s + f - 1) * math.factorial(a - 1) * math.factorial(b - 1),
    )
    likelihood = half**s * (1 - half) ** f
    lower = beta_ratio * _tail(a + s, a + b + s + f - 1, a + b + s + f - 1, half)
    lower /= _tail(a, a + b - 1, a + b - 1, half) * likelihood
    upper = beta_ratio * _tail(0, a + s - 1, a + b + s + f - 1, half)
    upper /= _tail(0, a - 1, a + b - 1, half) * likelihood
    assert _relative(e_lower, lower) <= PARITY
    assert _relative(e_upper, upper) <= PARITY
    assert e_value == (e_upper + e_lower) / 2.0


def test_all_failures_cross_at_the_same_step_as_all_successes_and_upper_alone_never_would() -> None:
    successes = _stream([1] * 12)
    failures = _stream([0] * 12)
    assert successes.stopped_sequence == failures.stopped_sequence == CROSSING_STEP
    assert failures.e_value == successes.e_value
    assert failures.e_upper == successes.e_lower and failures.e_lower == successes.e_upper
    assert all(wealths(POLICY, 0, f)[0] < 1.0 for f in range(1, 13))


def test_crossing_probability_under_the_null_respects_villes_bound() -> None:
    active: dict[tuple[int, int], float] = {(0, 0): 1.0}
    crossed = 0.0
    for trials in range(1, 13):
        following: dict[tuple[int, int], float] = {}
        for (_, s), probability in active.items():
            for outcome in (0, 1):
                e_value = wealths(POLICY, s + outcome, trials - s - outcome)[2]
                if e_value >= CONTRACT_PASS_E:
                    crossed += probability / 2
                else:
                    key = (trials, s + outcome)
                    following[key] = following.get(key, 0.0) + probability / 2
        active = following
    assert 0.0 < crossed <= 1 / CONTRACT_PASS_E


# --- stops ---


def test_budget_observations_stops_before_the_fourth_observation_and_is_warn_not_fail() -> None:
    run = _stream([1] * 10, max_observations=3)
    assert run.stop_reason == "budget_observations"
    assert (run.observations_used, run.stopped_sequence, run.trials) == (3, 3, 3)
    assert run.e_value < CONTRACT_PASS_E
    projection = projection_of(run)
    assert verdict(projection) == "warn"
    assert (projection.max_observations, projection.max_cost) == (3, None)


def test_budget_observations_equal_to_the_stream_length_completes_instead() -> None:
    run = _stream([1, 0, 1], max_observations=BUDGET)
    assert run.stop_reason == "complete"
    assert run.observations_used == BUDGET


def test_cost_budget_stops_before_overspending_and_records_the_cost_actually_used() -> None:
    observations = binary_observations([1] * 5, [0.4] * 5)
    run = run_stream(observations, replace(POLICY, max_cost=1.0))
    assert run.stop_reason == "budget_cost"
    assert (run.observations_used, run.stopped_sequence) == (2, 2)
    assert run.cost_used == pytest.approx(0.8)
    projection = projection_of(run)
    assert (projection.max_observations, projection.max_cost) == (None, 1.0)


def test_cost_budget_exactly_met_is_consumed_not_stopped() -> None:
    run = run_stream(binary_observations([1, 0], [0.5, 0.5]), replace(POLICY, max_cost=1.0))
    assert run.stop_reason == "complete"
    assert run.cost_used == 1.0
    projection = projection_of(run)
    assert projection.cost_used == projection.max_cost
    # Spending exactly the budget is no overspend.
    assert parse_projection(project(projection)) == projection


NO_COST = "max_cost set but an observation has no cost"
NO_TRIAL = "a stream needs at least one consumed trial"


def test_cost_budget_requires_a_cost_on_every_observation() -> None:
    observations = binary_observations([1, 0], [0.5, None])
    _assert_refused(
        "COST_REQUIRED", NO_COST, lambda: run_stream(observations, replace(POLICY, max_cost=1.0))
    )


def test_a_budget_that_stops_before_the_first_trial_is_an_error_not_a_run() -> None:
    observations = binary_observations([1, 1], [2.0, 0.1])
    _assert_refused(
        "NO_TRIALS", NO_TRIAL, lambda: run_stream(observations, replace(POLICY, max_cost=1.0))
    )
    _assert_refused(
        "NO_TRIALS",
        NO_TRIAL,
        lambda: eprocess.close_run(POLICY, (0, 0, 0, 2, 0, 0.0), "budget_cost"),
    )


def test_a_missing_cost_is_refused_before_the_stream_even_past_its_crossing() -> None:
    costs: list[float | None] = [0.1] * CROSSING_STEP + [None] * 2
    observations = binary_observations([1] * len(costs), costs)
    _assert_refused(
        "COST_REQUIRED", NO_COST, lambda: run_stream(observations, replace(POLICY, max_cost=100.0))
    )
    uncapped = run_stream(binary_observations([1] * len(costs), costs), POLICY)
    assert (uncapped.stop_reason, uncapped.cost_used) == ("threshold", pytest.approx(0.8))


def test_a_stream_that_ends_below_threshold_is_complete_and_warn() -> None:
    run = _stream([1, 0, 1, 0, 1])
    assert run.stop_reason == "complete"
    assert (run.observations_used, run.observations_total, run.stopped_sequence) == (5, 5, 5)
    assert (run.successes, run.trials) == (3, 5)
    assert verdict(projection_of(run)) == "warn"
    assert score(projection_of(run)) == round(1.0 - 1.0 / max(1.0, run.e_value), 6)


def test_a_score_below_one_wealth_is_zero_not_negative() -> None:
    run = run_stream((Observation(1, 2, None),), POLICY)
    assert run.e_value < 1.0
    assert score(projection_of(run)) == 0.0


def test_the_first_crossing_stops_the_stream_even_when_more_evidence_follows() -> None:
    run = _stream([1] * 30)
    assert run.observations_used == CROSSING_STEP
    assert run.e_value >= CONTRACT_PASS_E
    assert wealths(POLICY, CROSSING_STEP - 1, 0)[2] < CONTRACT_PASS_E


def test_threshold_boundary_equality_stops_exactly_at_pass_e() -> None:
    e_at_crossing = wealths(POLICY, CROSSING_STEP, 0)[2]
    run = _stream([1] * STREAM_LENGTH, pass_e=e_at_crossing)
    assert (run.stop_reason, run.observations_used) == ("threshold", CROSSING_STEP)
    projection = projection_of(run)
    assert projection.e_value == projection.pass_e
    assert parse_projection(project(projection)) == projection  # equality is a crossing
    assert verdict(projection) == "pass"
    just_above = _stream([1] * STREAM_LENGTH, pass_e=math.nextafter(e_at_crossing, math.inf))
    assert (just_above.stop_reason, just_above.observations_used) == (
        "threshold",
        CROSSING_STEP + 1,
    )


@pytest.mark.parametrize("n", [1, 12, 60])
def test_the_contract_priors_bound_the_mixture_away_from_zero(n: int) -> None:
    floor = 1 / (2 * (n + 1))
    assert all(wealths(POLICY, s, n - s)[2] >= floor for s in range(n + 1))


@settings(deadline=None, max_examples=60)
@given(
    outcomes=st.lists(st.integers(0, 1), min_size=1, max_size=40),
    p0=st.floats(1e-6, 1 - 1e-6),
)
def test_no_stream_under_the_contract_priors_is_absorbed(outcomes: list[int], p0: float) -> None:
    run = _stream(outcomes, p0=p0)
    assert run.stop_reason != "absorbed"
    assert run.e_value >= 1 / (2 * (run.trials + 1)) * (1 - PARITY)


def test_absorption_is_decided_by_zero_wealth_alone() -> None:
    assert eprocess.stop_after_update(0.0, CONTRACT_PASS_E) == "absorbed"
    assert eprocess.stop_after_update(CONTRACT_PASS_E, CONTRACT_PASS_E) == "threshold"
    assert eprocess.stop_after_update(math.nextafter(CONTRACT_PASS_E, 0.0), CONTRACT_PASS_E) is None
    assert eprocess.stop_after_update(5e-324, CONTRACT_PASS_E) is None


def test_an_absorbed_close_zeroes_every_wealth_and_is_not_measured() -> None:
    run = eprocess.close_run(POLICY, (3, 6, 6, 9, 6, 0.0), "absorbed")
    assert (run.e_value, run.e_upper, run.e_lower, run.stop_reason) == (0.0, 0.0, 0.0, "absorbed")
    projection = projection_of(run)
    assert projection.availability == "not_measured"
    assert projection.reason_codes == (WEALTH_ABSORBED,)
    assert verdict(projection) is None
    assert score(projection) is None
    assert parse_projection(project(projection)) == projection


def test_a_non_absorbed_close_keeps_the_wealths_from_the_counts() -> None:
    run = eprocess.close_run(POLICY, (3, 6, 6, 9, 6, 0.0), "complete")
    assert (run.e_upper, run.e_lower, run.e_value) == wealths(POLICY, 3, 3)
    assert run.stop_reason == "complete"


def test_overflowing_evidence_is_an_error_not_an_infinite_wealth() -> None:
    _assert_refused("E_VALUE_OVERFLOW", "s=5000, f=0", lambda: wealths(POLICY, 5000, 0))


# --- policy and observation validation ---


@pytest.mark.parametrize(
    ("field", "value", "code", "detail"),
    [
        ("p0", 0.0, "P0_OUT_OF_RANGE", "0.0"),
        ("p0", 1.0, "P0_OUT_OF_RANGE", "1.0"),
        ("p0", math.nan, "P0_OUT_OF_RANGE", "nan"),
        ("p0", True, "P0_OUT_OF_RANGE", "True"),
        ("p0", "0.5", "P0_OUT_OF_RANGE", "'0.5'"),
        ("prior_a", 1.0, "PRIOR_NOT_INTEGER", "a=1.0, b=1"),
        ("prior_b", True, "PRIOR_NOT_INTEGER", "a=1, b=True"),
        ("prior_a", 0, "PRIOR_NOT_POSITIVE", "a=0, b=1"),
        ("prior_b", -1, "PRIOR_NOT_POSITIVE", "a=1, b=-1"),
        ("pass_e", 0.999, "PASS_E_BELOW_ONE", "0.999"),
        ("pass_e", math.inf, "PASS_E_BELOW_ONE", "inf"),
        ("pass_e", True, "PASS_E_BELOW_ONE", "True"),
        ("max_observations", 0, "MAX_OBSERVATIONS_NOT_POSITIVE", "0"),
        ("max_observations", 1.5, "MAX_OBSERVATIONS_NOT_POSITIVE", "1.5"),
        ("max_observations", True, "MAX_OBSERVATIONS_NOT_POSITIVE", "True"),
        ("max_cost", 0.0, "MAX_COST_NOT_POSITIVE", "0.0"),
        ("max_cost", math.nan, "MAX_COST_NOT_POSITIVE", "nan"),
        ("max_cost", True, "MAX_COST_NOT_POSITIVE", "True"),
        ("null_id", "", "NULL_ID_EMPTY", "''"),
        ("null_id", 7, "NULL_ID_EMPTY", "7"),
    ],
)
def test_an_unusable_policy_is_refused_before_any_observation(
    field: str, value: object, code: str, detail: str
) -> None:
    policy = replace(POLICY, **{field: value})
    _assert_refused(code, detail, lambda: check_policy(policy))
    _assert_refused(code, detail, lambda: run_stream(binary_observations([1], None), policy))


def test_a_valid_policy_is_returned_unchanged_and_a_pass_e_of_one_is_allowed() -> None:
    assert check_policy(POLICY) is POLICY
    assert check_policy(replace(POLICY, pass_e=1.0, max_observations=1, max_cost=0.5)).pass_e == 1.0


@pytest.mark.parametrize(
    ("observation", "code", "detail"),
    [
        (Observation(0, 0, None), "TRIALS_NOT_POSITIVE", "0"),
        (Observation(1, True, None), "TRIALS_NOT_POSITIVE", "True"),
        (Observation(2, 1, None), "SUCCESSES_OUT_OF_RANGE", "2"),
        (Observation(-1, 1, None), "SUCCESSES_OUT_OF_RANGE", "-1"),
        (Observation(1.0, 1, None), "SUCCESSES_OUT_OF_RANGE", "1.0"),  # type: ignore[arg-type]
        (Observation(1, 1, -0.1), "COST_NEGATIVE", "-0.1"),
        (Observation(1, 1, math.inf), "COST_NEGATIVE", "inf"),
        (Observation(1, 1, True), "COST_NEGATIVE", "True"),  # type: ignore[arg-type]
    ],
)
def test_an_unusable_observation_is_refused(
    observation: Observation, code: str, detail: str
) -> None:
    _assert_refused(code, detail, lambda: check_observation(observation))
    _assert_refused(code, detail, lambda: run_stream((observation,), POLICY))


def test_a_free_observation_is_usable() -> None:
    free = Observation(1, 1, 0.0)
    assert check_observation(free) is free
    run = run_stream((free,), replace(POLICY, max_cost=1.0))
    assert (run.stop_reason, run.cost_used) == ("complete", 0.0)


def test_binary_observations_accept_only_zero_and_one_and_aligned_costs() -> None:
    assert binary_observations([1, 0], None) == (Observation(1, 1, None), Observation(0, 1, None))
    assert binary_observations([1], [0.25]) == (Observation(1, 1, 0.25),)
    _assert_refused("OUTCOME_NOT_BINARY", "2", lambda: binary_observations([2], None))
    _assert_refused("OUTCOME_NOT_BINARY", "True", lambda: binary_observations([True], None))
    _assert_refused(
        "OUTCOME_NOT_BINARY",
        "1.0",
        lambda: binary_observations([1.0], None),  # type: ignore[list-item]
    )
    _assert_refused(
        "COSTS_MISALIGNED",
        "1 costs for 2 outcomes",
        lambda: binary_observations([1, 0], [0.5]),
    )


def test_an_empty_stream_has_no_trials_and_is_an_error_not_a_read() -> None:
    _assert_refused("NO_TRIALS", NO_TRIAL, lambda: run_stream((), POLICY))


def test_every_refusal_carries_its_code_and_the_offending_input() -> None:
    error = EProcessError("SOME_CODE", "the detail")
    assert (error.code, error.args, str(error)) == (
        "SOME_CODE",
        ("SOME_CODE: the detail",),
        "SOME_CODE: the detail",
    )
    assert isinstance(error, ValueError)


def test_records_are_frozen() -> None:
    run = _stream([1, 0])
    with pytest.raises(FrozenInstanceError):
        run.e_value = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        projection_of(run).stop_reason = "threshold"  # type: ignore[misc]


# --- projection: read.v1 consistency parsing (not authentication) ---


def test_the_projection_has_exactly_the_read_v1_keys_and_spellings() -> None:
    block = _schema_block()
    payload = project(projection_of(_stream([1] * 10)))
    assert list(payload) == list(block["required"])
    assert set(block["properties"]) == set(payload)
    assert set(STOP_REASONS) == set(block["properties"]["stop_reason"]["enum"])
    assert payload["method"] == METHOD
    assert payload["availability"] == "measured"
    assert payload["reason_codes"] == []
    assert payload["max_observations"] is None and payload["max_cost"] is None
    reason_enum = cast("dict[str, Any]", json.loads(SCHEMA.read_text(encoding="utf-8")))["$defs"][
        "reason_codes"
    ]["items"]["enum"]
    assert WEALTH_ABSORBED in reason_enum


def test_project_and_parse_round_trip_for_every_stop_reason() -> None:
    runs = [
        _stream([1] * 10),
        _stream([1, 0, 1]),
        _stream([1] * 10, max_observations=3),
        run_stream(binary_observations([1] * 5, [0.4] * 5), replace(POLICY, max_cost=1.0)),
        eprocess.close_run(POLICY, (2, 4, 4, 4, 4, 0.0), "absorbed"),
    ]
    assert {run.stop_reason for run in runs} == set(STOP_REASONS)
    for run in runs:
        projection = projection_of(run)
        payload = project(projection)
        assert parse_projection(payload) == projection
        assert project(parse_projection(payload)) == payload
        assert json.loads(json.dumps(payload)) == payload


def _forge(payload: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**payload, **changes}


BELOW = project(projection_of(_stream([1, 0, 1])))  # complete: 3 of 3 used, e below pass_e


def test_restore_replays_independent_inputs_and_refuses_joint_wealth_upgrade() -> None:
    observations = binary_observations([1, 0], None)
    original = projection_of(run_stream(observations, POLICY))
    payload = project(original)
    payload.update(e_upper=20.0, e_lower=20.0, e_value=20.0, stop_reason="threshold")
    # Jointly rewritten values are internally consistent, not authenticated.
    assert verdict(parse_projection(payload)) == "pass"
    with pytest.raises(EProcessError) as excinfo:
        eprocess.restore(payload, observations=observations, policy=POLICY)
    assert excinfo.value.code == "PROJECTION_REPLAY_MISMATCH"


@pytest.mark.parametrize("outcomes", [[1, 0], [1] * 10, [0, 1, 1]])
def test_restore_replays_normal_inputs(outcomes: list[int]) -> None:
    observations = binary_observations(outcomes, None)
    original = projection_of(run_stream(observations, POLICY))
    assert eprocess.restore(project(original), observations=observations, policy=POLICY) == original


def test_restore_rejects_inputs_that_change_the_projected_result() -> None:
    observations = binary_observations([1, 0], None)
    payload = project(projection_of(run_stream(observations, POLICY)))
    for replay_observations, policy in (
        (binary_observations([1, 1], None), POLICY),
        (observations, replace(POLICY, pass_e=30.0)),
    ):
        with pytest.raises(EProcessError) as excinfo:
            eprocess.restore(payload, observations=replay_observations, policy=policy)
        assert excinfo.value.code == "PROJECTION_REPLAY_MISMATCH"


@pytest.mark.parametrize(
    ("changes", "code", "detail"),
    [
        ({"stop_reason": "threshold"}, "THRESHOLD_WITHOUT_CROSSING", repr(BELOW["e_value"])),
        (
            {"e_value": 0.0, "e_upper": 0.0, "e_lower": 0.0},
            "ZERO_WEALTH_LABELLED_MEASURED",
            "complete",
        ),
        (
            {"e_value": 21.0, "e_upper": 21.0, "e_lower": 21.0},
            "CROSSING_WITHOUT_THRESHOLD",
            "complete",
        ),
        ({"e_value": 0.5}, "E_VALUE_NOT_EQUAL_MIXTURE", "0.5"),
        ({"availability": "not_measured"}, "AVAILABILITY_MISMATCH", "complete: 'not_measured'"),
        ({"reason_codes": ["wealth_absorbed"]}, "REASON_CODES_MISMATCH", "('wealth_absorbed',)"),
        ({"reason_codes": "wealth_absorbed"}, "REASON_CODES_NOT_LIST", "'wealth_absorbed'"),
        ({"reason_codes": {"wealth_absorbed"}}, "REASON_CODES_NOT_LIST", "{'wealth_absorbed'}"),
        ({"reason_codes": [1]}, "REASON_CODES_NOT_STRINGS", "(1,)"),
        ({"reason_codes": ("x", 2)}, "REASON_CODES_NOT_STRINGS", "('x', 2)"),
        ({"stop_reason": "reset"}, "STOP_REASON_UNKNOWN", "'reset'"),
        ({"method": "sprt"}, "METHOD_MISMATCH", "'sprt'"),
        ({"null_id": ""}, "NULL_ID_EMPTY", "''"),
        ({"null_id": 7}, "NULL_ID_EMPTY", "7"),
        ({"e_value": math.nan}, "NUMBER_NOT_NONNEGATIVE_FINITE", "e_value=nan"),
        ({"e_upper": -1.0}, "NUMBER_NOT_NONNEGATIVE_FINITE", "e_upper=-1.0"),
        ({"e_lower": "1"}, "NUMBER_NOT_NONNEGATIVE_FINITE", "e_lower='1'"),
        ({"cost_used": math.inf}, "NUMBER_NOT_NONNEGATIVE_FINITE", "cost_used=inf"),
        ({"pass_e": 0.5}, "PASS_E_BELOW_ONE", "0.5"),
        ({"pass_e": math.inf}, "PASS_E_BELOW_ONE", "inf"),
        ({"pass_e": True}, "PASS_E_BELOW_ONE", "True"),
        ({"observations_used": 0}, "COUNT_OUT_OF_RANGE", "observations_used=0"),
        ({"observations_used": True}, "COUNT_OUT_OF_RANGE", "observations_used=True"),
        ({"observations_total": 0}, "COUNT_OUT_OF_RANGE", "observations_total=0"),
        ({"trials_used": 0}, "COUNT_OUT_OF_RANGE", "trials_used=0"),
        ({"stopped_sequence": -1}, "COUNT_OUT_OF_RANGE", "stopped_sequence=-1"),
        ({"observations_total": 2}, "USED_EXCEEDS_TOTAL", "3 > 2"),
        ({"stopped_sequence": 6}, "SEQUENCE_EXCEEDS_TOTAL", "6"),
        ({"max_observations": 2}, "USED_EXCEEDS_MAX_OBSERVATIONS", "3"),
        ({"max_observations": 0}, "MAX_OBSERVATIONS_NOT_POSITIVE", "0"),
        ({"max_cost": 0.0}, "MAX_COST_NOT_POSITIVE", "0.0"),
        ({"cost_used": 2.0, "max_cost": 1.0}, "COST_EXCEEDS_MAX_COST", "2.0"),
    ],
)
def test_a_forged_projection_is_refused(changes: dict[str, Any], code: str, detail: str) -> None:
    _assert_refused(code, detail, lambda: parse_projection(_forge(BELOW, **changes)))


def test_each_count_accepts_its_read_v1_minimum_and_refuses_one_below() -> None:
    block = _schema_block()["properties"]
    single = project(projection_of(_stream([0])))  # one observation, complete
    assert (single["observations_used"], single["observations_total"]) == (1, 1)
    for field in ("stopped_sequence", "observations_used", "observations_total", "trials_used"):
        floor = int(block[field]["minimum"])
        at_floor = _forge(single, **{field: floor})
        assert parse_projection(at_floor) == replace(projection_of(_stream([0])), **{field: floor})
        _assert_refused(
            "COUNT_OUT_OF_RANGE",
            f"{field}={floor - 1}",
            lambda field=field, floor=floor: parse_projection(_forge(single, **{field: floor - 1})),
        )


def test_a_pass_e_at_the_read_v1_minimum_parses() -> None:
    floor = float(_schema_block()["properties"]["pass_e"]["minimum"])
    run = _stream([1], pass_e=floor)
    projection = projection_of(run)
    assert (projection.pass_e, projection.stop_reason) == (floor, "threshold")
    assert parse_projection(project(projection)) == projection


def test_a_promoted_below_threshold_pass_and_an_active_zero_wealth_are_both_refused() -> None:
    payload = project(projection_of(_stream([1, 0, 1, 1])))
    wealth = repr(payload["e_value"])
    _assert_refused(
        "THRESHOLD_WITHOUT_CROSSING",
        wealth,
        lambda: parse_projection(_forge(payload, stop_reason="threshold")),
    )
    _assert_refused(
        "ZERO_WEALTH_LABELLED_MEASURED",
        "complete",
        lambda: parse_projection(_forge(payload, e_value=0.0, e_upper=0.0, e_lower=0.0)),
    )
    absorbed = {
        "stop_reason": "absorbed",
        "availability": "not_measured",
        "reason_codes": ["wealth_absorbed"],
    }
    _assert_refused(
        "ABSORBED_WITH_WEALTH", wealth, lambda: parse_projection(_forge(payload, **absorbed))
    )


def test_extra_or_missing_projection_fields_are_refused() -> None:
    payload = project(projection_of(_stream([1, 0])))
    _assert_refused(
        "PROJECTION_FIELDS",
        "extra=['verdict'], missing=[]",
        lambda: parse_projection(_forge(payload, verdict="pass")),
    )
    _assert_refused(
        "PROJECTION_FIELDS",
        "extra=[], missing=['e_lower', 'e_upper']",
        lambda: parse_projection(
            {k: v for k, v in payload.items() if k not in ("e_lower", "e_upper")}
        ),
    )


def test_check_projection_returns_the_same_record_and_score_verdict_agree_with_stop() -> None:
    projection = projection_of(_stream([1] * 10))
    assert check_projection(projection) is projection
    assert verdict(projection) == "pass"
    assert score(projection) == round(1.0 - 1.0 / projection.e_value, 6)
    below = projection_of(_stream([1, 0, 1]))
    assert verdict(below) == "warn"


# --- properties over arbitrary streams ---


@settings(deadline=None, max_examples=60)
@given(
    outcomes=st.lists(st.integers(0, 1), min_size=1, max_size=30),
    max_observations=st.one_of(st.none(), st.integers(1, 30)),
)
def test_any_stream_projects_to_a_parseable_consistent_state(
    outcomes: list[int], max_observations: int | None
) -> None:
    run = _stream(outcomes, max_observations=max_observations)
    projection = projection_of(run)
    payload = project(projection)
    assert parse_projection(payload) == projection
    assert projection.e_value == (projection.e_upper + projection.e_lower) / 2.0
    assert (projection.stop_reason == "threshold") == (projection.e_value >= CONTRACT_PASS_E)
    assert (verdict(projection) == "pass") == (projection.stop_reason == "threshold")
    assert projection.availability == "measured" and projection.reason_codes == ()
    assert 1 <= projection.observations_used <= projection.observations_total == len(outcomes)
    if max_observations is not None:
        assert projection.observations_used <= max_observations
    assert projection.stopped_sequence == projection.observations_used
    assert projection.trials_used == projection.observations_used


@settings(deadline=None, max_examples=40)
@given(outcomes=st.lists(st.integers(0, 1), min_size=1, max_size=20))
def test_permuting_a_stream_that_does_not_cross_gives_the_same_wealths(
    outcomes: list[int],
) -> None:
    run = _stream(outcomes)
    if run.stop_reason != "complete":
        return
    for permutation in itertools.islice(itertools.permutations(outcomes), 6):
        other = _stream(list(permutation))
        if other.stop_reason == "complete":
            assert (other.e_upper, other.e_lower, other.e_value) == (
                run.e_upper,
                run.e_lower,
                run.e_value,
            )
