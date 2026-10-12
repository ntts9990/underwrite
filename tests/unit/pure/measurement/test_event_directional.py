"""Directional stopping and primitive numerical identity controls."""

from fractions import Fraction

import pytest

from underwrite.measurement.directional import run_directional


def test_nth_crossing_wins_budget_and_diagnostics_keep_unused() -> None:
    crossing, total = 8, 12
    result = run_directional(
        [1] * total, p0=0.5, max_observations=crossing, desirable_direction="higher"
    )
    assert result["stop_reason"] == "upper_crossing"
    assert result["consumed_count"] == crossing
    assert result["unused_observed_count"] == total - crossing
    # Integral from .5 to 1 of 2*p^8/.5^8 dp; normalized uniform half-prior.
    assert result["e_upper"] == pytest.approx(float(Fraction(511, 9)))
    assert result["e_lower"] == pytest.approx(float(Fraction(1, 9)))


def test_budget_without_crossing() -> None:
    budget = 7
    result = run_directional([1] * 8, p0=0.5, max_observations=budget, desirable_direction="higher")
    assert result["stop_reason"] == "budget_observations"
    assert result["consumed_count"] == budget


def test_p0_is_not_fixed_to_half() -> None:
    result = run_directional([1], p0=0.25, max_observations=None, desirable_direction="higher")
    assert result["e_upper"] == pytest.approx(2.5)
    assert result["e_lower"] == pytest.approx(0.5)
