"""Standalone behavior and boundary checks."""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from hypothesis import given, settings
from hypothesis import strategies as st

from underwrite.measurement import jury
from underwrite.measurement.jury import (
    JuryError,
    JuryResult,
    Signal,
    check_signal,
    disagreement,
    measured_instruments,
    project,
    project_signal,
)

ROOT = repo_root(Path(__file__))
READ_SCHEMA = cast(
    "dict[str, Any]", json.loads((ROOT / "contracts/read.v1.schema.json").read_text())
)
DEFS = cast("dict[str, Any]", READ_SCHEMA["$defs"])
REASON_CODES = set(cast("list[str]", DEFS["reason_codes"]["items"]["enum"]))
NULL_ID = "binary:p=0.5"
LN3 = math.log(3)
E_TWO, E_FOUR, E_SIX = 2.0, 4.0, 6.0
MEAN_OF_2_4_6 = 4.0
PRODUCT_OF_2_4_6 = 48.0
PANEL_OF_THREE = 3
PAIR = 2
FIXTURE_E = 28.44
PRODUCT_OF_HALVES = 3.0
HUGE = 1e200


def _measured(
    e_value: float | None,
    *,
    verdict: str = "pass",
    confidence: float = 1.0,
    instrument: str = "deepeval",
    method: str = "eprocess",
) -> Signal:
    return Signal(
        method=method,
        instrument=instrument,
        availability="measured",
        verdict=cast("jury.Verdict", verdict),
        score=0.5,
        confidence=confidence,
        e_value=e_value,
        null_id=NULL_ID if e_value is not None else None,
        reason_codes=(),
    )


def _unmeasured(instrument: str = "judge", method: str = "krippendorff_alpha") -> Signal:
    return Signal(
        method=method,
        instrument=instrument,
        availability="not_measured",
        verdict=None,
        score=None,
        confidence=0.0,
        e_value=None,
        null_id=None,
        reason_codes=("signal_not_measured",),
    )


def _fuse_fixture(
    signals: list[Signal], *, combine: jury.Combine, independence: str | None
) -> JuryResult:
    # These preconstructed numeric fixtures declare only their e-value members.
    # Missingness tests below use an independent explicit roster, never this helper.
    roster = [(s.method, s.instrument) for s in signals if s.e_value is not None]
    return jury.fuse(signals, combine=combine, independence=independence, e_value_roster=roster)


def _mean(signals: list[Signal]) -> JuryResult:
    return _fuse_fixture(signals, combine="mean", independence=None)


@pytest.mark.parametrize("combine", ["mean", "product"])
def test_outcome_dependent_missingness_never_shrinks_the_declared_panel(
    combine: jury.Combine,
) -> None:
    # Each original e-value has null expectation 1 in two equiprobable worlds.
    # Selecting only its nonzero side would produce 2 in BOTH worlds.
    roster = (("eprocess", "a"), ("eprocess", "b"))
    for signals in (
        [_measured(2.0, instrument="a"), _unmeasured("b", "eprocess")],
        [_unmeasured("a", "eprocess"), _measured(2.0, instrument="b")],
        [_measured(2.0, instrument="a")],
        [_measured(2.0, instrument="a"), _measured(None, instrument="b")],
    ):
        result = jury.fuse(
            signals,
            combine=combine,
            independence="stated" if combine == "product" else None,
            e_value_roster=roster,
        )
        assert result.availability == "not_measured"
        assert result.e_value is None and result.members == 0
        assert result.signals == tuple(signals)
        assert result.reason_codes == ("signal_not_measured",)


def test_finite_huge_and_subnormal_means_survive_intermediate_arithmetic() -> None:
    for value in (1e308, math.ulp(0.0)):
        assert _mean([_measured(value), _measured(value, instrument="b")]).e_value == value


@pytest.mark.parametrize(
    ("signals", "roster", "code"),
    [
        ([_measured(2.0)], [("eprocess", "deepeval")] * 2, "DUPLICATE_ROSTER_SIGNAL"),
        ([_measured(2.0)] * 2, [("eprocess", "deepeval")], "DUPLICATE_SIGNAL"),
        ([_measured(2.0)], [], "UNDECLARED_E_VALUE"),
        ([], [("", "deepeval")], "MALFORMED_E_VALUE_ROSTER"),
    ],
)
def test_roster_and_submission_identity_errors_are_not_fused(
    signals: list[Signal], roster: list[tuple[str, str]], code: str
) -> None:
    with pytest.raises(JuryError) as excinfo:
        jury.fuse(signals, combine="mean", independence=None, e_value_roster=roster)
    assert excinfo.value.code == code


def test_roster_is_signal_level_not_instrument_level_and_preserves_descriptions() -> None:
    roster = (("eprocess", "a"), ("other", "a"))
    signals = [_measured(2.0, instrument="a"), _unmeasured("a", "other")]
    result = jury.fuse(signals, combine="mean", independence=None, e_value_roster=roster)
    assert result.e_value is None
    complete = [signals[0], _measured(6.0, instrument="a", method="other"), _unmeasured()]
    for order in itertools.permutations(complete):
        result = jury.fuse(order, combine="mean", independence=None, e_value_roster=roster)
        assert result.e_value == E_FOUR and result.members == PAIR
        assert result.signals == order


def test_empty_roster_and_inputs_are_not_a_measured_zero() -> None:
    result = jury.fuse([], combine="mean", independence=None, e_value_roster=[])
    assert result.availability == "not_measured" and result.e_value is None


# --- contract fit ---


def test_projections_carry_exactly_the_read_v1_keys_and_vocabulary() -> None:
    result = _mean([_measured(2.0), _unmeasured()])
    assert set(project(result)) == set(DEFS["jury"]["required"])
    assert set(project_signal(result.signals[0])) == set(DEFS["signal"]["required"])
    assert set(DEFS["jury"]["properties"]["combine"]["enum"]) == set(jury.COMBINES)
    assert set(DEFS["measured_verdict"]["enum"]) == set(jury.VERDICTS)
    assert {jury.REASON_SIGNAL_NOT_MEASURED, jury.REASON_NO_OBSERVATIONS} <= REASON_CODES
    projected = project(result)
    assert projected["combine"] == "mean"
    assert projected["availability"] == "measured"
    assert projected["e_value"] == E_TWO
    assert projected["members"] == 1
    assert projected["independence"] is None
    assert projected["reason_codes"] == ["signal_not_measured"]
    assert isinstance(projected["reason_codes"], list)
    unmeasured = project_signal(result.signals[1])
    assert unmeasured["availability"] == "not_measured"
    assert unmeasured["verdict"] is None and unmeasured["score"] is None
    assert unmeasured["e_value"] is None and unmeasured["null_id"] is None
    assert unmeasured["reason_codes"] == ["signal_not_measured"]
    measured = project_signal(result.signals[0])
    assert measured == {
        "method": "eprocess",
        "instrument": "deepeval",
        "availability": "measured",
        "verdict": "pass",
        "score": 0.5,
        "confidence": 1.0,
        "e_value": 2.0,
        "null_id": NULL_ID,
        "reason_codes": [],
    }


# --- mean fusion ---


def test_three_instrument_mean_is_four_in_every_permutation() -> None:
    signals = [
        _measured(2.0, instrument="a", verdict="warn"),
        _measured(4.0, instrument="b"),
        _measured(6.0, instrument="c"),
    ]
    expected = _mean(signals)
    assert expected.e_value == MEAN_OF_2_4_6
    assert expected.members == PANEL_OF_THREE
    assert expected.availability == "measured"
    assert expected.reason_codes == ()
    for order in itertools.permutations(signals):
        result = _mean(list(order))
        assert result.e_value == MEAN_OF_2_4_6
        assert result.disagreement == expected.disagreement
        assert project(result) == project(expected)
        assert result.signals == tuple(order)


def test_mean_uses_a_correctly_rounded_sum_so_order_cannot_change_the_last_bit() -> None:
    values = [0.1, 0.2, 0.3]
    signals = [_measured(v, instrument=f"i{i}") for i, v in enumerate(values)]
    exact = math.fsum(values) / len(values)
    # A left-to-right fold differs by order and, sorted, still differs from the rounded sum.
    assert {(a + b + c) / len(values) for a, b, c in itertools.permutations(values)} != {exact}
    assert (0.1 + 0.2 + 0.3) / len(values) != exact
    for order in itertools.permutations(signals):
        assert _mean(list(order)).e_value == exact


def test_deterministic_checks_without_an_e_value_are_not_averaged_in_nor_called_absent() -> None:
    result = _mean(
        [_measured(2.0), _measured(6.0, instrument="b"), _measured(None, instrument="c")]
    )
    assert result.e_value == MEAN_OF_2_4_6
    assert result.members == PAIR
    assert result.reason_codes == ()
    assert result.signals[2].availability == "measured"


def test_a_failed_judge_is_carried_through_and_casts_no_vote() -> None:
    with_judge = _mean([_measured(2.0), _measured(6.0, instrument="b"), _unmeasured()])
    without = _mean([_measured(2.0), _measured(6.0, instrument="b")])
    assert with_judge.e_value == without.e_value == MEAN_OF_2_4_6
    assert with_judge.disagreement == without.disagreement
    assert with_judge.members == without.members == PAIR
    assert with_judge.reason_codes == ("signal_not_measured",)
    assert without.reason_codes == ()
    assert with_judge.signals[2] == _unmeasured()
    assert len(with_judge.signals) == PANEL_OF_THREE


def test_a_single_e_value_is_its_own_mean() -> None:
    result = _mean([_measured(FIXTURE_E)])
    assert result.e_value == FIXTURE_E
    assert result.members == 1
    assert result.disagreement == 0.0


# --- product fusion ---


def test_product_needs_independence_evidence_and_mean_refuses_it() -> None:
    signals = [_measured(2.0), _measured(4.0, instrument="b"), _measured(6.0, instrument="c")]
    stated = _fuse_fixture(signals, combine="product", independence="disjoint case sets")
    assert stated.e_value == PRODUCT_OF_2_4_6
    assert stated.combine == "product"
    assert stated.independence == "disjoint case sets"
    assert project(stated)["independence"] == "disjoint case sets"
    for evidence in (None, "", "   "):
        with pytest.raises(JuryError) as excinfo:
            _fuse_fixture(signals, combine="product", independence=evidence)
        assert excinfo.value.code == "INDEPENDENCE_NOT_STATED"
        assert str(excinfo.value) == "INDEPENDENCE_NOT_STATED: product fusion needs its evidence"
    with pytest.raises(JuryError) as excinfo:
        _fuse_fixture(signals, combine="mean", independence="not needed")
    assert excinfo.value.code == "INDEPENDENCE_WITHOUT_PRODUCT"
    assert str(excinfo.value) == "INDEPENDENCE_WITHOUT_PRODUCT: mean fusion needs no independence"


def test_product_is_order_invariant_and_overflow_is_an_error_not_a_number() -> None:
    signals = [
        _measured(1.5, instrument="a"),
        _measured(0.25, instrument="b"),
        _measured(8.0, instrument="c"),
    ]
    for order in itertools.permutations(signals):
        assert (
            _fuse_fixture(list(order), combine="product", independence="stated").e_value
            == PRODUCT_OF_HALVES
        )
    huge = [_measured(HUGE, instrument="a"), _measured(HUGE, instrument="b")]
    with pytest.raises(JuryError) as excinfo:
        _fuse_fixture(huge, combine="product", independence="stated")
    assert excinfo.value.code == "E_VALUE_OVERFLOW"
    assert str(excinfo.value) == f"E_VALUE_OVERFLOW: {[HUGE, HUGE]!r}"
    assert _fuse_fixture(huge, combine="mean", independence=None).e_value == HUGE


def test_product_rounding_is_fixed_by_sorting_so_input_order_cannot_leak_into_the_bits() -> None:
    values = [0.1, 0.2, 0.3]
    signals = [_measured(v, instrument=f"i{i}") for i, v in enumerate(values)]
    exact = math.prod(sorted(values))
    assert 0.1 * 0.2 * 0.3 != 0.3 * 0.2 * 0.1  # the raw products differ in their last bit
    for order in itertools.permutations(signals):
        assert _fuse_fixture(list(order), combine="product", independence="stated").e_value == exact


def test_the_jury_projection_is_the_full_read_v1_object_for_both_availabilities() -> None:
    measured = _mean([_measured(E_TWO), _measured(E_SIX, instrument="b")])
    assert project(measured) == {
        "combine": "mean",
        "availability": "measured",
        "e_value": MEAN_OF_2_4_6,
        "disagreement": 0.0,
        "members": PAIR,
        "independence": None,
        "reason_codes": [],
    }
    assert project(_mean([])) == {
        "combine": "mean",
        "availability": "not_measured",
        "e_value": None,
        "disagreement": None,
        "members": 0,
        "independence": None,
        "reason_codes": ["no_observations"],
    }


def test_an_unknown_combine_is_refused_before_any_signal_is_read() -> None:
    with pytest.raises(JuryError) as excinfo:
        _fuse_fixture([], combine=cast("jury.Combine", "max"), independence=None)
    assert excinfo.value.code == "UNKNOWN_COMBINE"
    assert str(excinfo.value) == "UNKNOWN_COMBINE: 'max'"


# --- nothing to fuse ---


def test_no_signals_at_all_is_not_measured_with_no_observations() -> None:
    result = _mean([])
    assert result.availability == "not_measured"
    assert result.e_value is None and result.disagreement is None
    assert result.members == 0
    assert result.reason_codes == ("no_observations",)
    assert result.signals == ()
    assert project(result)["e_value"] is None


def test_signals_without_a_measurable_e_value_are_not_measured_never_a_smaller_success() -> None:
    for signals in (
        [_unmeasured()],
        [_measured(None)],
        [_unmeasured(), _measured(None, instrument="b")],
    ):
        result = _mean(signals)
        assert result.availability == "not_measured"
        assert result.e_value is None and result.disagreement is None
        assert result.members == 0
        assert result.reason_codes == ("signal_not_measured",)
        assert result.signals == tuple(signals)


def test_product_of_nothing_keeps_its_stated_independence_but_no_value() -> None:
    result = _fuse_fixture([_unmeasured()], combine="product", independence="stated")
    assert result.combine == "product"
    assert result.independence == "stated"
    assert result.availability == "not_measured"
    assert result.e_value is None


def test_e_values_under_different_nulls_cannot_be_averaged() -> None:
    other = replace(_measured(4.0, instrument="b"), null_id="binary:p=0.9")
    for panel in ([_measured(2.0), other], [other, _measured(2.0)]):
        with pytest.raises(JuryError) as excinfo:
            _mean(panel)
        assert excinfo.value.code == "NULL_ID_MISMATCH"
        # both nulls are named, sorted, whatever the panel order
        assert str(excinfo.value) == f"NULL_ID_MISMATCH: {sorted([NULL_ID, 'binary:p=0.9'])!r}"


def test_disagreement_is_zero_for_agreement_and_one_for_three_equal_camps() -> None:
    assert disagreement([_measured(2.0), _measured(4.0, instrument="b")]) == 0.0
    assert disagreement([_measured(2.0)]) == 0.0
    assert disagreement([]) == 0.0
    three = [
        _measured(2.0, verdict="pass", instrument="a"),
        _measured(2.0, verdict="warn", instrument="b"),
        _measured(2.0, verdict="fail", instrument="c"),
    ]
    assert disagreement(three) == pytest.approx(1.0, abs=1e-12)


def test_full_agreement_is_positive_zero_so_a_serialized_read_never_shows_minus_zero() -> None:
    agreed = [_measured(2.0), _measured(4.0, instrument="b", confidence=0.3)]
    value = disagreement(agreed)
    assert value == 0.0
    assert math.copysign(1.0, value) == 1.0
    projected = project(_mean(agreed))
    assert math.copysign(1.0, cast("float", projected["disagreement"])) == 1.0
    assert '"disagreement": 0.0' in json.dumps(projected)


def test_a_subnormal_weight_that_underflows_in_its_share_contributes_nothing() -> None:
    # The two default confidences of 1.0 make the total weight 2.0, and 5e-324 / 2.0
    # underflows to 0.0; that entropy term is the limit 0, not log(0).
    tiny = math.ulp(0.0)
    panel = [
        _measured(2.0),
        _measured(2.0, instrument="b"),
        _measured(2.0, verdict="warn", instrument="c", confidence=tiny),
    ]
    assert tiny / 2.0 == 0.0
    assert disagreement(panel) == 0.0
    assert _mean(panel).disagreement == 0.0


def test_disagreement_of_two_equal_camps_is_log2_over_log3() -> None:
    two = [_measured(2.0, verdict="pass"), _measured(2.0, verdict="fail", instrument="b")]
    assert disagreement(two) == pytest.approx(math.log(2) / LN3, abs=1e-12)


def test_disagreement_is_confidence_weighted() -> None:
    weighted = [
        _measured(2.0, verdict="pass", confidence=0.75),
        _measured(2.0, verdict="warn", confidence=0.25, instrument="b"),
    ]
    expected = -(0.75 * math.log(0.75) + 0.25 * math.log(0.25)) / LN3
    assert disagreement(weighted) == pytest.approx(expected, abs=1e-12)
    assert disagreement(weighted) < math.log(2) / LN3


def test_zero_confidence_and_unmeasured_signals_carry_no_weight() -> None:
    silent = [
        _measured(2.0, verdict="pass", confidence=0.0),
        _measured(2.0, verdict="fail", confidence=0.0, instrument="b"),
    ]
    assert disagreement(silent) == 0.0
    mixed = [_measured(2.0, verdict="pass"), _unmeasured()]
    assert disagreement(mixed) == 0.0
    loud = [_measured(2.0, verdict="pass"), _measured(2.0, verdict="fail", instrument="b")]
    assert disagreement([*loud, _unmeasured()]) == disagreement(loud)


def test_disagreement_does_not_depend_on_the_order_of_same_verdict_weights() -> None:
    panel = [
        _measured(None, verdict="pass", confidence=0.1, instrument="a"),
        _measured(None, verdict="pass", confidence=0.1, instrument="b"),
        _measured(None, verdict="pass", confidence=0.4, instrument="c"),
        _measured(None, verdict="warn", confidence=0.4, instrument="d"),
    ]
    assert 0.1 + 0.1 + 0.4 != 0.4 + 0.1 + 0.1  # a running sum would carry the order
    values = {disagreement(list(order)) for order in itertools.permutations(panel)}
    assert len(values) == 1
    expected = -(0.6 * math.log(0.6) + 0.4 * math.log(0.4)) / LN3
    assert values.pop() == pytest.approx(expected, abs=1e-15)


def test_the_fused_result_reports_disagreement_over_the_whole_measured_panel() -> None:
    signals = [
        _measured(2.0, verdict="pass"),
        _measured(None, verdict="fail", instrument="deterministic"),
    ]
    result = _mean(signals)
    assert result.members == 1
    assert result.disagreement == pytest.approx(math.log(2) / LN3, abs=1e-12)


# --- signal validation ---


def test_check_signal_returns_the_signal_it_accepted() -> None:
    measured = _measured(2.0)
    assert check_signal(measured) is measured
    unmeasured = _unmeasured()
    assert check_signal(unmeasured) is unmeasured
    assert check_signal(_measured(None)).e_value is None


@pytest.mark.parametrize(
    ("field", "value", "code", "message"),
    [
        ("method", "", "EMPTY_METHOD", "EMPTY_METHOD"),
        ("instrument", "", "EMPTY_INSTRUMENT", "EMPTY_INSTRUMENT"),
        ("availability", "partial", "UNKNOWN_AVAILABILITY", "UNKNOWN_AVAILABILITY: 'partial'"),
        ("confidence", 1.5, "CONFIDENCE_OUT_OF_RANGE", "CONFIDENCE_OUT_OF_RANGE: 1.5"),
        ("confidence", -0.1, "CONFIDENCE_OUT_OF_RANGE", "CONFIDENCE_OUT_OF_RANGE: -0.1"),
        ("confidence", math.nan, "CONFIDENCE_OUT_OF_RANGE", "CONFIDENCE_OUT_OF_RANGE: nan"),
        ("confidence", True, "CONFIDENCE_OUT_OF_RANGE", "CONFIDENCE_OUT_OF_RANGE: True"),
        (
            "reason_codes",
            ("x", "x"),
            "DUPLICATE_REASON_CODES",
            "DUPLICATE_REASON_CODES: ('x', 'x')",
        ),
        ("reason_codes", ("",), "MALFORMED_REASON_CODES", "MALFORMED_REASON_CODES: ('',)"),
        ("verdict", "promote", "UNKNOWN_VERDICT", "UNKNOWN_VERDICT: 'promote'"),
        ("verdict", None, "UNKNOWN_VERDICT", "UNKNOWN_VERDICT: None"),
        ("score", 1.0001, "SCORE_OUT_OF_RANGE", "SCORE_OUT_OF_RANGE: 1.0001"),
        ("score", None, "SCORE_OUT_OF_RANGE", "SCORE_OUT_OF_RANGE: None"),
        ("e_value", -1e-9, "E_VALUE_OUT_OF_RANGE", "E_VALUE_OUT_OF_RANGE: -1e-09"),
        ("e_value", math.inf, "E_VALUE_OUT_OF_RANGE", "E_VALUE_OUT_OF_RANGE: inf"),
        ("e_value", True, "E_VALUE_OUT_OF_RANGE", "E_VALUE_OUT_OF_RANGE: True"),
        ("null_id", "", "E_VALUE_WITHOUT_NULL_ID", "E_VALUE_WITHOUT_NULL_ID: eprocess"),
        ("null_id", None, "E_VALUE_WITHOUT_NULL_ID", "E_VALUE_WITHOUT_NULL_ID: eprocess"),
    ],
)
def test_a_malformed_measured_signal_is_refused_with_its_code_and_offending_value(
    field: str, value: object, code: str, message: str
) -> None:
    with pytest.raises(JuryError) as excinfo:
        check_signal(replace(_measured(2.0), **{field: value}))
    assert excinfo.value.code == code
    assert str(excinfo.value) == message
    with pytest.raises(JuryError) as through_fuse:
        _mean([replace(_measured(2.0), **{field: value})])
    assert str(through_fuse.value) == message


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("verdict", "pass", "UNMEASURED_SIGNAL_CARRIES_VALUES"),
        ("score", 0.0, "UNMEASURED_SIGNAL_CARRIES_VALUES"),
        ("e_value", 0.0, "UNMEASURED_SIGNAL_CARRIES_VALUES"),
        ("reason_codes", (), "UNMEASURED_SIGNAL_WITHOUT_REASON"),
    ],
)
def test_an_unmeasured_signal_may_not_carry_values_or_omit_its_reason(
    field: str, value: object, code: str
) -> None:
    with pytest.raises(JuryError) as excinfo:
        check_signal(replace(_unmeasured(), **{field: value}))
    assert excinfo.value.code == code
    assert str(excinfo.value) == f"{code}: krippendorff_alpha"  # names the offending method


def test_boundary_values_are_accepted() -> None:
    check_signal(replace(_measured(0.0), confidence=0.0, score=0.0))
    check_signal(replace(_measured(0.0), confidence=1.0, score=1.0))
    assert _mean([_measured(0.0)]).e_value == 0.0


def test_error_text_carries_the_code_and_detail() -> None:
    error = JuryError("UNKNOWN_COMBINE", "'max'")
    assert error.code == "UNKNOWN_COMBINE"
    assert str(error) == "UNKNOWN_COMBINE: 'max'"
    assert str(JuryError("EMPTY_METHOD")) == "EMPTY_METHOD"


# --- what availability receives ---


def test_measured_instruments_are_sorted_unique_and_exclude_the_unmeasured() -> None:
    signals = [
        _measured(2.0, instrument="retrieval"),
        _measured(None, instrument="deepeval"),
        _measured(3.0, instrument="deepeval", method="other"),
        _unmeasured(instrument="judge"),
    ]
    assert measured_instruments(signals) == ("deepeval", "retrieval")
    assert measured_instruments([]) == ()


# --- properties ---


_verdicts = st.sampled_from(["pass", "warn", "fail"])
_confidences = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
_e_values = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)


@settings(deadline=None, max_examples=60)
@given(st.lists(st.tuples(_verdicts, _confidences, _e_values), min_size=1, max_size=6))
def test_mean_lies_between_the_extremes_and_disagreement_stays_in_the_unit_interval(
    rows: list[tuple[str, float, float]],
) -> None:
    signals = [
        _measured(e, verdict=v, confidence=c, instrument=f"i{i}")
        for i, (v, c, e) in enumerate(rows)
    ]
    result = _mean(signals)
    values = [e for _v, _c, e in rows]
    assert result.e_value is not None
    assert min(values) - 1e-9 <= result.e_value <= max(values) + 1e-9
    assert result.disagreement is not None
    assert 0.0 <= result.disagreement <= 1.0 + 1e-12
    assert result.members == len(rows)


@settings(deadline=None, max_examples=40)
@given(st.permutations(list(range(5))))
def test_any_permutation_of_the_panel_projects_identically(order: list[int]) -> None:
    base = [
        _measured(2.0, verdict="pass", confidence=0.9, instrument="a"),
        _measured(4.0, verdict="warn", confidence=0.4, instrument="b"),
        _measured(6.0, verdict="fail", confidence=0.7, instrument="c"),
        _measured(None, verdict="pass", confidence=1.0, instrument="d"),
        _unmeasured(instrument="e"),
    ]
    permuted = [base[i] for i in order]
    assert project(_mean(permuted)) == project(_mean(base))
