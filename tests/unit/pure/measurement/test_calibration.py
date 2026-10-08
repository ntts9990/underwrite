"""Standalone behavior and boundary checks."""

from __future__ import annotations

import inspect
import itertools
import json
import math
from dataclasses import FrozenInstanceError
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from hypothesis import given, settings
from hypothesis import strategies as st

from underwrite.measurement import calibration
from underwrite.measurement.calibration import (
    MEASURED,
    NOT_MEASURED,
    REASON_BIN_BELOW_MIN_N,
    REASON_N_BELOW_K,
    BrierMeasure,
    CalibrationError,
    ECEMeasure,
    Pair,
    PassKMeasure,
    bin_index,
    brier,
    ece,
    make_pair,
    pass_k,
    project_calibration,
    project_measure,
)

ROOT = repo_root(Path(__file__))
READ_SCHEMA = cast(
    "dict[str, Any]", json.loads((ROOT / "contracts/read.v1.schema.json").read_text())
)
DEFS = cast("dict[str, Any]", READ_SCHEMA["$defs"])

FOUR = [make_pair(0.9, 1), make_pair(0.2, 0), make_pair(0.6, 1), make_pair(0.3, 0)]
TWO_BINS = [make_pair(0.1, 0), make_pair(0.3, 1), make_pair(0.7, 1), make_pair(0.9, 1)]
SINGLE_PAIR_BRIER = 0.5625  # (0.25 - 1) ** 2


def _exact_ece(pairs: list[Pair], bins: int) -> Fraction:
    """Standalone behavior and boundary checks."""
    counts = [0] * bins
    predictions = [Fraction(0)] * bins
    labels = [Fraction(0)] * bins
    for pair in pairs:
        index = min(int(pair.prediction * bins), bins - 1)
        counts[index] += 1
        predictions[index] += Fraction(pair.prediction)
        labels[index] += pair.label
    total = len(pairs)
    return sum(
        (
            Fraction(counts[i], total) * abs(labels[i] / counts[i] - predictions[i] / counts[i])
            for i in range(bins)
            if counts[i]
        ),
        Fraction(0),
    )


def _exact_brier(pairs: list[Pair]) -> Fraction:
    return sum(((Fraction(p.prediction) - p.label) ** 2 for p in pairs), Fraction(0)) / len(pairs)


# --- contract fit: the rendered dicts are read.v1's measure / calibration objects ---


def test_rendered_views_carry_exactly_the_contract_keys() -> None:
    measure_keys = set(cast("list[str]", DEFS["measure"]["required"]))
    ece_extra = set(cast("list[str]", DEFS["calibration"]["properties"]["ece"]["required"]))
    pass_k_extra = set(cast("list[str]", DEFS["calibration"]["properties"]["pass_k"]["required"]))
    rendered = project_calibration(brier(FOUR), ece(TWO_BINS, 2, 2), pass_k(2, 3, 2))
    assert set(rendered) == set(cast("list[str]", DEFS["calibration"]["required"]))
    assert set(rendered["brier"]) == measure_keys
    assert set(rendered["ece"]) == measure_keys | ece_extra
    assert set(rendered["pass_k"]) == measure_keys | pass_k_extra
    states = set(cast("list[str]", DEFS["availability_state"]["enum"]))
    reasons = set(cast("list[str]", DEFS["reason_codes"]["items"]["enum"]))
    assert {MEASURED, NOT_MEASURED} == states
    assert {REASON_BIN_BELOW_MIN_N, REASON_N_BELOW_K} <= reasons
    for view in rendered.values():
        assert view["availability"] in states
        assert isinstance(view["reason_codes"], list)
        assert set(view["reason_codes"]) <= reasons


def test_project_measure_renders_values_and_reasons_faithfully() -> None:
    measured = project_measure(pass_k(2, 3, 2))
    assert measured == {
        "availability": MEASURED,
        "value": pytest.approx(1 / 3),
        "n": 3,
        "reason_codes": [],
        "k": 2,
    }
    absent = project_measure(ece(TWO_BINS, 2, 3))
    assert absent == {
        "availability": NOT_MEASURED,
        "value": None,
        "n": 4,
        "reason_codes": [REASON_BIN_BELOW_MIN_N],
        "bins": 2,
        "min_bin_n": 3,
    }
    assert project_measure(brier([make_pair(0.25, 1)])) == {
        "availability": MEASURED,
        "value": SINGLE_PAIR_BRIER,
        "n": 1,
        "reason_codes": [],
    }


# --- pairs ---


@pytest.mark.parametrize(
    "prediction",
    [-0.1, 1.0000001, float("nan"), float("inf"), True, "0.5", None],
)
def test_a_prediction_outside_the_unit_interval_is_refused(prediction: object) -> None:
    with pytest.raises(CalibrationError, match="INVALID_PREDICTION"):
        make_pair(prediction, 1)


@pytest.mark.parametrize("label", [2, -1, 0.5, True, False, "1", None, float("nan")])
def test_a_label_that_is_not_zero_or_one_is_refused(label: object) -> None:
    with pytest.raises(CalibrationError, match="INVALID_LABEL"):
        make_pair(0.5, label)


def test_boundary_predictions_and_float_labels_are_accepted_and_normalised() -> None:
    assert make_pair(0, 1.0) == Pair(0.0, 1)
    assert make_pair(1, 0.0) == Pair(1.0, 0)
    assert type(make_pair(1, 0.0).label) is int
    assert type(make_pair(1, 0.0).prediction) is float


def test_a_pair_built_around_make_pair_is_still_validated_by_every_view() -> None:
    bad = [Pair(1.5, 1)]
    for view in (lambda: brier(bad), lambda: ece(bad, 2, 1)):
        with pytest.raises(CalibrationError, match="INVALID_PREDICTION"):
            view()


def test_empty_input_is_an_error_not_a_measurement() -> None:
    with pytest.raises(CalibrationError, match="NO_PAIRS"):
        brier([])
    with pytest.raises(CalibrationError, match="NO_PAIRS"):
        ece([], 2, 1)


def test_records_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        brier(FOUR).value = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        make_pair(0.5, 1).label = 0  # type: ignore[misc]


# --- Brier ---


def test_brier_matches_the_pinned_mean_squared_error() -> None:
    measure = brier(FOUR)
    assert (measure.availability, measure.n, measure.reason_codes) == (MEASURED, 4, ())
    assert measure.value == pytest.approx(0.075, rel=1e-12, abs=1e-15)
    assert measure.value == pytest.approx(float(_exact_brier(FOUR)), rel=1e-12, abs=1e-15)
    assert brier(FOUR) == BrierMeasure(MEASURED, measure.value, 4, ())


def test_brier_of_a_single_pair_and_of_perfect_predictions() -> None:
    assert brier([make_pair(0.25, 1)]).value == SINGLE_PAIR_BRIER
    assert brier([make_pair(1.0, 1), make_pair(0.0, 0)]).value == 0.0
    assert brier([make_pair(0.0, 1), make_pair(1.0, 0)]).value == 1.0


def test_brier_is_always_measured_whatever_the_bin_situation() -> None:
    measure = brier(TWO_BINS)
    assert measure.availability == MEASURED
    assert measure.reason_codes == ()
    assert measure.n == len(TWO_BINS)


# --- bins and ECE ---


@pytest.mark.parametrize(
    ("prediction", "bins", "expected"),
    [(0.0, 10, 0), (1.0, 10, 9), (0.5, 2, 1), (0.49, 2, 0), (0.999, 1, 0), (0.1, 10, 1)],
)
def test_bin_index_is_fixed_width_with_the_top_edge_folded_into_the_last_bin(
    prediction: float, bins: int, expected: int
) -> None:
    assert bin_index(prediction, bins) == expected


def test_ece_matches_the_pinned_weighted_gap_when_every_populated_bin_is_large_enough() -> None:
    measure = ece(TWO_BINS, 2, 2)
    assert measure.availability == MEASURED
    assert measure.value == pytest.approx(0.25, rel=1e-12, abs=1e-15)
    assert measure.value == pytest.approx(float(_exact_ece(TWO_BINS, 2)), rel=1e-12, abs=1e-15)
    assert measure.n == len(TWO_BINS)
    assert measure.reason_codes == ()
    assert (measure.bins, measure.min_bin_n, measure.bin_counts) == (2, 2, (2, 2))


def test_ece_is_not_measured_when_one_populated_bin_is_below_the_minimum() -> None:
    measure = ece(TWO_BINS, 2, 3)
    assert measure == ECEMeasure(NOT_MEASURED, None, 4, (REASON_BIN_BELOW_MIN_N,), 2, 3, (2, 2))


def test_ece_min_bin_n_boundary_is_inclusive() -> None:
    assert ece(TWO_BINS, 2, 2).availability == MEASURED
    assert ece(TWO_BINS, 2, 3).availability == NOT_MEASURED


def test_empty_bins_do_not_block_ece() -> None:
    measure = ece(TWO_BINS, 10, 1)
    assert measure.availability == MEASURED
    assert measure.bin_counts == (0, 1, 0, 1, 0, 0, 0, 1, 0, 1)
    assert measure.value == pytest.approx(float(_exact_ece(TWO_BINS, 10)), rel=1e-12, abs=1e-15)
    assert measure.value == pytest.approx(0.25 * (0.1 + 0.7 + 0.3 + 0.1))


def test_a_single_sparse_bin_among_full_ones_is_enough_to_withhold_ece() -> None:
    pairs = [*TWO_BINS, make_pair(0.55, 1)]
    counts = ece(pairs, 10, 1).bin_counts
    assert counts == (0, 1, 0, 1, 0, 1, 0, 1, 0, 1)
    withheld = ece(pairs, 2, 3)
    assert withheld.bin_counts == (2, 3)
    assert withheld.availability == NOT_MEASURED


def test_ece_with_one_bin_is_the_absolute_mean_gap() -> None:
    measure = ece(FOUR, 1, 4)
    assert measure.value == pytest.approx(0.0, abs=1e-15)  # mean prediction 0.5, event rate 0.5
    assert measure.value == pytest.approx(float(_exact_ece(FOUR, 1)), rel=1e-12, abs=1e-15)
    assert ece([make_pair(0.9, 0)], 1, 1).value == pytest.approx(0.9)


@pytest.mark.parametrize("bins", [0, -1, 2.0, True, "10", None])
def test_bins_must_be_a_positive_integer(bins: object) -> None:
    with pytest.raises(CalibrationError, match="INVALID_BINS"):
        ece(TWO_BINS, cast("int", bins), 1)


@pytest.mark.parametrize("min_bin_n", [0, -3, 1.0, True, None])
def test_min_bin_n_must_be_a_positive_integer_with_no_default(min_bin_n: object) -> None:
    with pytest.raises(CalibrationError, match="INVALID_MIN_BIN_N"):
        ece(TWO_BINS, 2, cast("int", min_bin_n))


def test_the_views_take_their_inputs_explicitly() -> None:
    for function in (ece, pass_k):
        assert all(
            parameter.default is inspect.Parameter.empty
            for parameter in inspect.signature(function).parameters.values()
        )


# --- pass^k ---


@pytest.mark.parametrize(
    ("successes", "trials", "k", "expected"),
    [
        (3, 3, 3, 1.0),
        (2, 3, 3, 0.0),
        (2, 3, 2, 1 / 3),
        (0, 5, 1, 0.0),
        (5, 5, 1, 1.0),
        (4, 5, 1, 0.8),
        (4, 6, 2, 6 / 15),
        (1, 1, 1, 1.0),
    ],
)
def test_pass_k_is_the_exact_combinatorial_estimator(
    successes: int, trials: int, k: int, expected: float
) -> None:
    measure = pass_k(successes, trials, k)
    assert measure == PassKMeasure(MEASURED, expected, trials, (), k, successes)
    assert measure.value == float(Fraction(math.comb(successes, k), math.comb(trials, k)))


def test_pass_k_with_fewer_trials_than_k_is_not_measured_not_zero() -> None:
    measure = pass_k(2, 2, 3)
    assert measure == PassKMeasure(NOT_MEASURED, None, 2, (REASON_N_BELOW_K,), 3, 2)
    assert pass_k(0, 0, 1) == PassKMeasure(NOT_MEASURED, None, 0, (REASON_N_BELOW_K,), 1, 0)


def test_pass_k_boundary_at_n_equal_k() -> None:
    assert pass_k(3, 3, 3).availability == MEASURED
    assert pass_k(3, 3, 4).availability == NOT_MEASURED


@pytest.mark.parametrize(
    ("successes", "trials", "k", "code"),
    [
        (4, 3, 1, "INVALID_SUCCESSES"),
        (-1, 3, 1, "INVALID_SUCCESSES"),
        (1.0, 3, 1, "INVALID_SUCCESSES"),
        (True, 3, 1, "INVALID_SUCCESSES"),
        (1, -1, 1, "INVALID_TRIALS"),
        (1, 3.0, 1, "INVALID_TRIALS"),
        (1, True, 1, "INVALID_TRIALS"),
        (1, 3, 0, "INVALID_K"),
        (1, 3, 1.0, "INVALID_K"),
        (1, 3, True, "INVALID_K"),
    ],
)
def test_pass_k_refuses_non_integers_and_impossible_counts(
    successes: object, trials: object, k: object, code: str
) -> None:
    with pytest.raises(CalibrationError, match=code):
        pass_k(cast("int", successes), cast("int", trials), cast("int", k))


# --- properties ---


_pairs = st.lists(
    st.tuples(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        st.sampled_from([0, 1]),
    ),
    min_size=1,
    max_size=24,
).map(lambda raw: [make_pair(p, label) for p, label in raw])


@settings(deadline=None, max_examples=60)
@given(_pairs, st.integers(min_value=1, max_value=6))
def test_measured_values_stay_in_the_unit_interval_and_match_the_pinned_arithmetic(
    pairs: list[Pair], bins: int
) -> None:
    brier_measure = brier(pairs)
    assert 0.0 <= cast("float", brier_measure.value) <= 1.0
    assert brier_measure.value == pytest.approx(float(_exact_brier(pairs)), rel=1e-12, abs=1e-12)
    measure = ece(pairs, bins, 1)
    assert measure.availability == MEASURED
    assert 0.0 <= cast("float", measure.value) <= 1.0
    assert measure.value == pytest.approx(float(_exact_ece(pairs, bins)), rel=1e-12, abs=1e-12)
    assert sum(measure.bin_counts) == len(pairs)


@settings(deadline=None, max_examples=60)
@given(_pairs, st.integers(min_value=1, max_value=6), st.integers(min_value=1, max_value=8))
def test_ece_availability_follows_the_populated_bin_minimum_exactly(
    pairs: list[Pair], bins: int, min_bin_n: int
) -> None:
    measure = ece(pairs, bins, min_bin_n)
    populated = [count for count in measure.bin_counts if count]
    if min(populated) >= min_bin_n:
        assert measure.availability == MEASURED
        assert measure.reason_codes == ()
    else:
        assert measure.availability == NOT_MEASURED
        assert measure.value is None
        assert measure.reason_codes == (REASON_BIN_BELOW_MIN_N,)


@settings(deadline=None, max_examples=80)
@given(st.integers(min_value=0, max_value=12), st.integers(min_value=1, max_value=12))
def test_pass_k_is_monotone_in_successes_and_reduces_to_the_rate_at_k_one(
    trials: int, k: int
) -> None:
    values = [pass_k(c, trials, k) for c in range(trials + 1)]
    if trials < k:
        assert all(m.availability == NOT_MEASURED for m in values)
        return
    assert all(m.availability == MEASURED for m in values)
    numbers = [cast("float", m.value) for m in values]
    assert numbers == sorted(numbers)
    if k == 1:
        assert numbers == pytest.approx([c / trials for c in range(trials + 1)])


def test_ece_of_a_permuted_input_is_identical() -> None:
    values = {ece(list(order), 2, 2).value for order in itertools.permutations(TWO_BINS)}
    assert len(values) == 1


def test_module_binds_no_numeric_constant_a_threshold_could_hide_in() -> None:
    numeric = {
        name: value
        for name, value in vars(calibration).items()
        if name.isupper() and isinstance(value, (int, float))
    }
    assert numeric == {}
    assert all(
        isinstance(value, str) for name, value in vars(calibration).items() if name.isupper()
    )
