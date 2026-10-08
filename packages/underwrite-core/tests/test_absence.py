"""Standalone behavior and boundary checks."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from underwrite_core.absence import AbsenceError, AbsenceOutcome, Exemption, check_population


def test_count_at_or_above_min_is_pass() -> None:
    verdict = check_population(1, 0)
    assert verdict.outcome is AbsenceOutcome.PASS
    assert verdict.count == 1
    assert verdict.min_count == 0
    assert verdict.reason is None
    assert verdict.expiry is None


def test_empty_population_without_exemption_fails() -> None:
    verdict = check_population(0, 1)
    assert verdict.outcome is AbsenceOutcome.EMPTY_POPULATION
    assert verdict.count == 0
    assert verdict.min_count == 1


def test_valid_exemption_before_expiry_is_skipped_with_reason() -> None:
    exemption = Exemption(
        reason="sample population unavailable", expiry="2099-01-01", adr="example-rule"
    )
    verdict = check_population(0, 1, exemption=exemption, now="2026-09-04")
    assert verdict.outcome is AbsenceOutcome.SKIPPED_WITH_REASON
    assert verdict.count == 0
    assert verdict.min_count == 1
    assert verdict.reason == exemption.reason
    assert verdict.expiry == exemption.expiry


def test_expired_exemption_is_exemption_expired() -> None:
    exemption = Exemption(reason="sample population unavailable", expiry="2020-01-01")
    verdict = check_population(0, 1, exemption=exemption, now="2026-09-04")
    assert verdict.outcome is AbsenceOutcome.EXEMPTION_EXPIRED
    assert verdict.count == 0
    assert verdict.min_count == 1


def test_expiry_equal_to_now_counts_as_expired() -> None:
    exemption = Exemption(reason="boundary check", expiry="2026-09-04")
    verdict = check_population(0, 1, exemption=exemption, now="2026-09-04")
    assert verdict.outcome is AbsenceOutcome.EXEMPTION_EXPIRED


def test_exemption_without_now_raises_now_required() -> None:
    exemption = Exemption(reason="r", expiry="2099-01-01")
    with pytest.raises(AbsenceError, match=r"^NOW_REQUIRED$"):
        check_population(0, 1, exemption=exemption)


def test_empty_reason_raises_invalid_exemption() -> None:
    exemption = Exemption(reason="   ", expiry="2099-01-01")
    with pytest.raises(AbsenceError, match=r"^INVALID_EXEMPTION$"):
        check_population(0, 1, exemption=exemption, now="2026-09-04")


def test_malformed_expiry_raises_invalid_exemption() -> None:
    exemption = Exemption(reason="r", expiry="not-a-date")
    with pytest.raises(AbsenceError, match=r"^INVALID_EXEMPTION$"):
        check_population(0, 1, exemption=exemption, now="2026-09-04")


@given(
    min_count=st.integers(min_value=0, max_value=1000),
    extra=st.integers(min_value=0, max_value=1000),
)
def test_count_at_or_above_min_is_always_pass_regardless_of_exemption(
    min_count: int, extra: int
) -> None:
    count = min_count + extra
    without_exemption = check_population(count, min_count)
    assert without_exemption.outcome is AbsenceOutcome.PASS

    exemption = Exemption(reason="r", expiry="2000-01-01")
    with_exemption = check_population(count, min_count, exemption=exemption, now="2099-01-01")
    assert with_exemption.outcome is AbsenceOutcome.PASS
