"""Standalone behavior and boundary checks."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import random
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from typing import cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes

from underwrite.instrument.evidence import aa, selfcheck
from underwrite.instrument.evidence.aa import AACase, AAError, AAPolicy, measure_aa
from underwrite.instrument.evidence.paired import PermutationEvidence
from underwrite.instrument.evidence.selfcheck import (
    CheckObservation,
    SelfCheckError,
    compare_selfcheck,
)

ROOT = repo_root(Path(__file__))
SEED = cast(
    int, json.loads((ROOT / "contracts/statistical_seed.v1.schema.json").read_text())["const"]
)
POLICY = AAPolicy(SEED, 8, 10000, 0.05, 2, 1, 10_000_000)
DIGEST = "sha256:" + "a" * 64


class CustomString(str):
    pass


class CustomInt(int):
    pass


class CustomFloat(float):
    pass


class CustomCase(AACase):
    pass


class CustomCheck(CheckObservation):
    pass


class CustomPolicy(AAPolicy):
    pass


class CustomCases(list[AACase]):
    pass


class CustomOutcomes(tuple[bool, ...]):
    pass


def cases(n: int, outcomes: tuple[bool, ...] = (True, False)) -> list[AACase]:
    return [AACase(f"C{i:02}", outcomes, False) for i in range(n)]


@pytest.mark.parametrize(
    ("items", "reason", "usable", "excluded"),
    [
        ([], "empty_population", 0, 0),
        ([AACase("A", (True,), False)], "insufficient_repeats", 0, 1),
        ([AACase("A", (True, False), False)], "insufficient_cases", 1, 0),
        ([AACase("A", (), True)], "diagnostic_only_population", 0, 0),
    ],
)
def test_absence_is_not_zero_success(
    items: list[AACase], reason: str, usable: int, excluded: int
) -> None:
    result = measure_aa(items, policy=POLICY)
    assert result.reason == reason
    assert result.support == "not_measured"
    assert result.performed_iterations == result.reserved_work == 0
    assert result.usable_cases == usable
    assert result.excluded_insufficient_repeat_cases == excluded
    assert result.false_positive_rate is result.vacuous_rate is result.tie_rate is None
    assert result.known_vacuous_fraction is result.p_value_summary is None
    assert result.iterations == ()
    assert result.requested_iterations == POLICY.iterations


def test_explicit_minima_and_zero_work_absence() -> None:
    policy = replace(POLICY, minimum_cases=3, max_work=1)
    assert measure_aa(cases(2), policy=policy).reason == "insufficient_cases"
    policy = replace(POLICY, iterations=1, minimum_iterations=2, max_work=1)
    result = measure_aa(cases(2), policy=policy)
    assert result.reason == "insufficient_iterations"
    assert result.reserved_work == result.performed_iterations == 0
    at_boundary = measure_aa(cases(2), policy=replace(policy, minimum_iterations=1, max_work=12))
    assert (at_boundary.performed_iterations, at_boundary.reserved_work) == (1, 12)


@pytest.mark.parametrize("outcome", [True, False])
def test_constant_corpus_keeps_observed_zero_separate_from_vacuity(outcome: bool) -> None:
    result = measure_aa(cases(3, (outcome,) * 4), policy=POLICY)
    assert result.false_positive_rate == 0
    assert result.vacuous_rate == result.known_vacuous_fraction == result.tie_rate == 1
    assert result.reason == "vacuous" and result.support == "not_measured"
    assert result.p_value_summary == (1.0, 1.0, 1.0)
    assert result.significant_iterations == result.detectable_exact_iterations == 0
    assert result.vacuous_iterations == POLICY.iterations
    assert result.unresolved_detectability_iterations == 0
    assert result.tied_comparisons == 3 * POLICY.iterations
    assert result.non_tied_comparisons == 0


def test_exact_detectability_and_significance_are_strict() -> None:
    items = cases(3)
    boundary = measure_aa(items, policy=replace(POLICY, significance_level=0.25))
    above = measure_aa(items, policy=replace(POLICY, significance_level=math.nextafter(0.25, 1)))
    assert boundary.vacuous_iterations == POLICY.iterations
    assert boundary.significant_iterations == 0
    assert above.detectable_exact_iterations == POLICY.iterations
    assert above.vacuous_iterations == 0
    assert above.support == "available" and above.reason is None
    assert above.significant_iterations > 0
    assert above.false_positive_rate == above.significant_iterations / POLICY.iterations
    assert above.vacuous_rate == 0
    expected_minimum = 0.25
    assert all(row.minimum_attainable_p == expected_minimum for row in above.iterations)


def test_exact_vacuous_and_detectable_iterations_partition_real_fixed_n() -> None:
    result = measure_aa(
        cases(2, (True, True, False, False)),
        policy=replace(POLICY, iterations=30, significance_level=0.6),
    )
    assert 0 < result.vacuous_iterations < result.performed_iterations
    assert 0 < result.detectable_exact_iterations < result.performed_iterations
    assert result.unresolved_detectability_iterations == 0
    assert (
        result.vacuous_iterations + result.detectable_exact_iterations
        == result.performed_iterations
    )
    assert result.vacuous_rate == result.known_vacuous_fraction == result.vacuous_iterations / 30
    assert result.false_positive_rate == result.significant_iterations / 30
    assert (result.tied_comparisons + result.non_tied_comparisons, result.usable_cases) == (60, 2)
    p_values = sorted(row.p_value for row in result.iterations)
    assert result.p_value_summary == (p_values[0], (p_values[14] + p_values[15]) / 2, p_values[-1])


def test_real_mc_unresolved_significance_and_vacuity_without_fake_support() -> None:
    # Thirteen usable cases remain fixed. One changing case creates both allzero

    items = cases(12, (True, True)) + [AACase("vary", (True, True, False, False), False)]
    result = measure_aa(items, policy=replace(POLICY, iterations=3))
    assert 0 < result.vacuous_iterations < result.performed_iterations
    assert result.detectable_exact_iterations == 0
    assert result.unresolved_detectability_iterations == 3 - result.vacuous_iterations
    assert result.vacuous_rate is None and result.vacuous_rate_reason == "unresolved_detectability"
    assert result.known_vacuous_fraction == result.vacuous_iterations / 3
    assert result.support == "not_measured" and result.reason == "unresolved_detectability"
    assert result.reserved_work == 3 * (28 + 13 * 10000)
    assert result.usable_cases == len(items)
    for row in result.iterations:
        assert row.completed_permutation_iterations == POLICY.permutation_iterations
        assert row.resolution_floor == 1 / 10001
    significant = measure_aa(
        cases(13), policy=replace(POLICY, iterations=1, significance_level=0.99)
    )
    assert significant.significant_iterations == 1
    assert significant.unresolved_detectability_iterations == 1
    assert significant.false_positive_rate == 1
    assert significant.vacuous_rate is None
    assert significant.reason == "unresolved_detectability"
    tiny = measure_aa(cases(13), policy=replace(POLICY, iterations=1, significance_level=1 / 10001))
    assert tiny.vacuous_iterations == 1 and tiny.vacuous_rate == 1
    assert tiny.significant_iterations == 0 and tiny.reason == "vacuous"


def test_real_shared_permutation_called_without_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    real = aa.permutation_evidence
    calls: list[int] = []

    def capture(
        differences: Sequence[int | float], *, seed: int, iterations: int, max_work: int
    ) -> PermutationEvidence:
        calls.append(seed)
        return real(differences, seed=seed, iterations=iterations, max_work=max_work)

    monkeypatch.setattr(aa, "permutation_evidence", capture)
    result = measure_aa(cases(2), policy=POLICY)
    assert len(calls) == result.performed_iterations
    assert len(set(calls)) == POLICY.iterations
    assert result.method == "aa-random-halves-mean-signflip-v1"
    assert "bootstrap" not in inspect.getsource(aa.measure_aa)


def test_work_limit_rejects_before_rng(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("RNG before atomic validation/reservation")

    with monkeypatch.context() as context:
        context.setattr(aa.random, "Random", forbidden)
        with pytest.raises(AAError) as error:
            measure_aa(cases(2), policy=replace(POLICY, max_work=95))
        assert error.value.code == "COMPUTATION_LIMIT"
        assert "reserved=96" in str(error.value)
        assert "max_work=95" in str(error.value)
    result = measure_aa(cases(2), policy=replace(POLICY, max_work=96))
    assert (result.reserved_work, result.max_work) == (96, 96)


def test_exact_support_boundary_reserves_exhaustive_work_not_mc_draws() -> None:
    # Twelve cases have 4096 labeled sign transforms, including tied transforms.
    # The next case switches to Monte Carlo; that separate path is exercised above.
    population = cases(12, (True, True))
    transforms = 2 ** len(population)
    reserved = sum(len(row.outcomes) for row in population) + len(population) * transforms
    policy = replace(POLICY, iterations=1, max_work=reserved)
    result = measure_aa(population, policy=policy)
    assert result.reserved_work == reserved
    assert result.performed_iterations == 1
    assert result.iterations[0].completed_permutation_iterations == transforms
    assert result.iterations[0].resolution_floor is None
    assert result.iterations[0].minimum_attainable_p == 1.0
    assert result.iterations[0].detectability == "vacuous"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed", True),
        ("seed", 42.0),
        ("seed", CustomInt(SEED)),
        ("iterations", True),
        ("iterations", 0),
        ("iterations", 1.0),
        ("minimum_iterations", 0),
        ("minimum_iterations", False),
        ("minimum_cases", 1),
        ("minimum_cases", True),
        ("permutation_iterations", 9999),
        ("permutation_iterations", True),
        ("permutation_iterations", 10000.0),
        ("significance_level", 0.0),
        ("significance_level", 1.0),
        ("significance_level", True),
        ("significance_level", float("nan")),
        ("significance_level", float("inf")),
        ("significance_level", CustomFloat(0.05)),
        ("max_work", 0),
        ("max_work", True),
        ("max_work", 1.0),
    ],
)
def test_invalid_policy_even_for_empty_population(field: str, value: object) -> None:
    with pytest.raises(AAError) as error:
        measure_aa([], policy=replace(POLICY, **{field: value}))
    expected_code = f"INVALID_{field.upper()}"
    assert error.value.code == expected_code
    assert str(error.value) == expected_code


@pytest.mark.parametrize(
    ("item", "code", "detail"),
    [
        (AACase("", (True, False), False), "INVALID_CASE_ID", ""),
        (AACase(" ", (), False), "INVALID_CASE_ID", ""),
        (AACase("e\u0301", (), False), "INVALID_CASE_ID", ""),
        (AACase("\ud800", (), False), "INVALID_CASE_ID", ""),
        (AACase(CustomString("A"), (), False), "INVALID_CASE_ID", ""),
        (AACase("A", cast(tuple[bool, ...], [True, False]), False), "INVALID_OUTCOMES", "A"),
        (AACase("A", CustomOutcomes((True, False)), False), "INVALID_OUTCOMES", "A"),
        (AACase("A", cast(tuple[bool, ...], (1, False)), False), "INVALID_OUTCOMES", "A"),
        (AACase("A", cast(tuple[bool, ...], (None, False)), False), "INVALID_OUTCOMES", "A"),
        (AACase("A", cast(tuple[bool, ...], ("yes", False)), False), "INVALID_OUTCOMES", "A"),
        (AACase("A", (), cast(bool, 0)), "INVALID_DIAGNOSTIC", "A"),
        (CustomCase("A", (), False), "INVALID_CASE", ""),
        (cast(AACase, {"case_id": "A"}), "INVALID_CASE", ""),
    ],
)
def test_malformed_case_fails_atomically(
    item: AACase, code: str, detail: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("malformed late record must fail before RNG")

    monkeypatch.setattr(aa.random, "Random", forbidden)
    with pytest.raises(AAError) as error:
        measure_aa([*cases(2), item], policy=POLICY)
    assert error.value.code == code
    assert str(error.value) == (f"{code}: {detail}" if detail else code)


def test_duplicate_and_nonstandard_population_rejected() -> None:
    with pytest.raises(AAError) as error:
        measure_aa([*cases(2), cases(2)[0]], policy=POLICY)
    assert error.value.code == "DUPLICATE_CASE_ID"
    assert str(error.value) == "DUPLICATE_CASE_ID: C00"
    with pytest.raises(AAError) as error:
        measure_aa(cast(Sequence[AACase], iter(cases(2))), policy=POLICY)
    assert error.value.code == "INVALID_POPULATION"
    with pytest.raises(AAError) as error:
        measure_aa([], policy=cast(AAPolicy, {}))
    assert error.value.code == "INVALID_POLICY"
    with pytest.raises(AAError, match="INVALID_POLICY"):
        measure_aa([], policy=CustomPolicy(SEED, 8, 10000, 0.05, 2, 1, 10000))
    with pytest.raises(AAError, match="INVALID_POPULATION"):
        measure_aa(CustomCases(cases(2)), policy=POLICY)


def test_explicit_diagnostic_seed_is_echoed_without_global_rng_mutation() -> None:
    before = random.getstate()
    diagnostic = replace(POLICY, seed=-7)
    result = measure_aa(cases(3), policy=diagnostic)
    assert result.policy.seed == diagnostic.seed
    assert result == measure_aa(tuple(cases(3)), policy=diagnostic)
    assert random.getstate() == before


def observation(identifier: str, expected: bool, observed: bool | None) -> CheckObservation:
    return CheckObservation(identifier, expected, observed, "recorded://canary", DIGEST)


def test_selfcheck_observations_and_biased_canary_are_not_authority() -> None:
    checks = [
        observation("ok", True, True),
        observation("false", False, False),
        observation("biased", False, True),
        observation("wrong", True, False),
        observation("missing", True, None),
    ]
    result = compare_selfcheck(checks)
    assert (result.total, result.observed, result.mismatches, result.missing) == (5, 4, 2, 1)
    assert [item.state for item in result.checks] == [
        "mismatched",
        "matched",
        "not_measured",
        "matched",
        "mismatched",
    ]
    assert result.reason == "missing_observations"
    assert result.support == "not_measured"
    assert compare_selfcheck(checks[:2]).support == "available"
    assert compare_selfcheck(checks[2:3]).support == "available"  # measured mismatch
    assert result == compare_selfcheck(list(reversed(checks)))
    assert all(row.source_digest == DIGEST for row in result.checks)
    assert "supplied_provenance_not_independently_verified" in result.limitations
    serialized = asdict(result)
    assert not {"passed", "calibrated", "approved", "rungs", "score"} & serialized.keys()


def test_selfcheck_preserves_every_supplied_fact_and_provenance_without_endorsement() -> None:
    checks = [
        CheckObservation("z", False, True, "file:///not-opened/check-z", "sha256:" + "1" * 64),
        CheckObservation("a", True, None, "https://not-fetched.invalid/a", "sha256:" + "2" * 64),
        CheckObservation("m", False, False, "recorded://check-m", "sha256:" + "3" * 64),
    ]
    result = compare_selfcheck(checks)
    assert tuple(asdict(row) for row in result.checks) == tuple(
        asdict(row) for row in sorted(checks, key=lambda row: row.check_id)
    )
    assert {
        "supplied_provenance_not_independently_verified",
        "no_evaluator_execution_or_quality_authority",
    } <= set(result.limitations)
    assert result.checks[0].state == "not_measured"
    assert result.checks[1].state == "matched"
    assert result.checks[2].state == "mismatched"


def test_aa_keeps_scientific_and_authority_limitations_on_measured_and_absent_results() -> None:
    limitations = {
        "conditional_random_halves_of_one_recorded_corpus",
        "not_independent_future_trials_or_population_calibration",
        "scoring_execution_and_exchangeability_not_verified",
        "source_sign_test_replaced_by_mean_signflip",
        "sorted_cases_and_derived_substreams_differ_from_source",
        "no_adequacy_acceptance_or_deployment_authority",
    }
    for population in ([], cases(3)):
        result = measure_aa(population, policy=replace(POLICY, significance_level=0.5))
        assert limitations <= set(result.limitations)


def test_empty_selfcheck_is_not_measured() -> None:
    result = compare_selfcheck([])
    assert result.total == result.observed == result.mismatches == result.missing == 0
    assert result.checks == ()
    assert result.reason == "empty_population" and result.support == "not_measured"


def test_support_is_not_measured_exactly_when_a_reason_exists() -> None:
    """Availability and its cause are one fact, not two that may disagree.

    ``not_measured`` is one of the three states AGENTS.md rule 10 forbids folding into
    a neighbour, so neither engine may report an unavailable result without naming why,
    nor name a reason while still calling the result available. Each case below is
    reported elsewhere in this file on its own; what is stated here is that the two
    fields move together, over every way either engine has of withholding.
    """
    detectable = replace(POLICY, significance_level=math.nextafter(0.25, 1))
    results = [
        measure_aa([], policy=POLICY),
        measure_aa([AACase("A", (True,), False)], policy=POLICY),
        measure_aa(cases(3, (True,) * 4), policy=POLICY),
        measure_aa(cases(3), policy=detectable),
        compare_selfcheck([]),
        compare_selfcheck([observation("a", True, None)]),
        compare_selfcheck([observation("a", True, True)]),
    ]
    assert [row.support == "not_measured" for row in results] == [
        row.reason is not None for row in results
    ]
    # Both halves of the rule are exercised: a one-sided population would satisfy it.
    assert {row.support for row in results} == {"not_measured", "available"}


def test_actual_aa_result_and_deliberately_biased_output_form_a_canary() -> None:
    original = measure_aa(cases(2, (True, True)), policy=replace(POLICY, iterations=1))
    biased = replace(original, iterations=(replace(original.iterations[0], p_value=0.001),))
    raw = canonical_bytes(json.loads(json.dumps(asdict(original), allow_nan=False)))
    check = CheckObservation(
        "recorded-identical-arms",
        original.false_positive_rate == 0,
        biased.false_positive_rate == 0,
        "fixture://constructed-identical-arms/aa",
        "sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    result = compare_selfcheck([check])
    assert result.checks[0].state == "mismatched"
    assert (result.observed, result.mismatches, result.missing) == (1, 1, 0)
    assert original.false_positive_rate == 0 and biased.false_positive_rate == 1
    # This is deliberate output corruption of an actual pure-engine result,
    # not a production bias-injection callback or real-model performance claim.


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("check_id", ""),
        ("check_id", "e\u0301"),
        ("check_id", "\ud800"),
        ("check_id", CustomString("A")),
        ("expected", 1),
        ("expected", None),
        ("observed", 0),
        ("observed", "false"),
        ("source_ref", ""),
        ("source_ref", "e\u0301"),
        ("source_ref", None),
        ("source_ref", "\ud800"),
        ("source_digest", ""),
        ("source_digest", "sha256:" + "A" * 64),
        ("source_digest", "sha256:" + "0" * 63),
        ("source_digest", DIGEST + "\n"),
        ("source_digest", None),
        ("source_digest", CustomString(DIGEST)),
    ],
)
def test_selfcheck_malformed_provenance_or_values(field: str, value: object) -> None:
    with pytest.raises(SelfCheckError) as error:
        compare_selfcheck([replace(observation("A", True, True), **{field: value})])
    expected_code = (
        "INVALID_BOOLEAN" if field in ("expected", "observed") else f"INVALID_{field.upper()}"
    )
    assert error.value.code == expected_code
    detail = ": A" if field in ("source_digest", "expected", "observed") else ""
    assert str(error.value) == expected_code + detail


def test_selfcheck_duplicate_or_invalid_containers() -> None:
    row = observation("A", True, True)
    with pytest.raises(SelfCheckError) as error:
        compare_selfcheck([row, row])
    assert error.value.code == "DUPLICATE_CHECK_ID"
    assert str(error.value) == "DUPLICATE_CHECK_ID: A"
    with pytest.raises(SelfCheckError) as error:
        compare_selfcheck(cast(Sequence[CheckObservation], iter([row])))
    assert error.value.code == "INVALID_POPULATION"
    with pytest.raises(SelfCheckError) as error:
        compare_selfcheck([cast(CheckObservation, {})])
    assert error.value.code == "INVALID_CHECK"
    with pytest.raises(SelfCheckError, match="INVALID_CHECK"):
        compare_selfcheck([CustomCheck("A", True, True, "source", DIGEST)])


def test_output_ownership_and_explicit_settings() -> None:
    assert type(SEED) is int
    items = cases(2)
    result = measure_aa(items, policy=POLICY)
    previous = asdict(result)
    assert canonical_bytes(json.loads(json.dumps(previous, allow_nan=False)))
    assert all(
        "seed" not in asdict(row) and "differences" not in asdict(row) for row in result.iterations
    )
    items.clear()
    assert asdict(result) == previous
    with pytest.raises(FrozenInstanceError):
        result.policy.seed = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.iterations[0].p_value = 0  # type: ignore[misc]
    assert type(result.iterations) is tuple
    checks = [observation("A", True, None)]
    self_result = compare_selfcheck(checks)
    checks.clear()
    assert self_result.total == 1
    with pytest.raises(FrozenInstanceError):
        self_result.checks[0].state = "matched"  # type: ignore[misc]
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in inspect.signature(AAPolicy).parameters.values()
    )
    assert not hasattr(selfcheck, "project_ladder")
