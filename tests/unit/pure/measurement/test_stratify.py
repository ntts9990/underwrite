"""Standalone behavior and boundary checks."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import cast

import pytest
from _repo_paths import repo_root
from hypothesis import given, settings
from hypothesis import strategies as st

from underwrite.measurement import stratify as module
from underwrite.measurement.stratify import (
    MEASURED,
    MEASURED_VERDICTS,
    NOT_MEASURED,
    REASON_CELL_BELOW_MIN_N,
    REASON_CODES,
    REASON_NO_OBSERVATIONS,
    Case,
    Cell,
    Outcome,
    StratifyError,
    group,
    make_case,
    project,
    stratify,
)

ROOT = repo_root(Path(__file__))
SCHEMA = cast("dict[str, object]", json.loads((ROOT / "contracts/read.v1.schema.json").read_text()))
DEFS = cast("dict[str, dict[str, object]]", SCHEMA["$defs"])
REASON_ENUM = cast("list[str]", cast("dict[str, object]", DEFS["reason_codes"]["items"])["enum"])
SIGNAL_NOT_MEASURED = "signal_not_measured"


def _mean_pass(members: tuple[Case, ...]) -> Outcome:
    """An order-invariant stand-in for the injected measurement: share of payload == 1."""
    share = sum(1 for case in members if case.payload == 1) / len(members)
    return ("pass" if share > 0.5 else "warn", share)  # noqa: PLR2004 -- half, a test oracle


def _case(case_id: str, locale: str, payload: object = 1, **extra: str) -> Case:
    return make_case(case_id, {"locale": locale, **extra}, payload)


def _cases() -> list[Case]:
    return [
        _case("k1", "ko"),
        _case("k2", "ko", 0),
        _case("k3", "ko"),
        _case("e1", "en"),
        _case("e2", "en"),
        _case("e3", "en", 0),
        _case("j1", "ja"),
    ]


def _parts(cell: Cell) -> tuple[object, ...]:
    return (cell.n, cell.availability, cell.verdict, cell.score, cell.reason_codes)


class _Counter:
    def __init__(self) -> None:
        self.calls: list[tuple[Case, ...]] = []

    def __call__(self, members: tuple[Case, ...]) -> Outcome:
        self.calls.append(members)
        return _mean_pass(members)


# --- contract fit ---


def test_project_renders_exactly_the_stratum_shape_in_both_branches() -> None:
    stratum = DEFS["stratum"]
    required = cast("list[str]", stratum["required"])
    unmeasured = cast(
        "dict[str, dict[str, object]]", cast("dict[str, object]", stratum["else"])["properties"]
    )
    assert unmeasured["verdict"] == {"type": "null"}
    assert unmeasured["score"] == {"type": "null"}
    cells = stratify(_cases(), ["locale"], 3, _mean_pass, requested=[{"locale": "fr"}])
    assert {cell.availability for cell in cells} == {MEASURED, NOT_MEASURED}
    for cell in cells:
        rendered = project(cell)
        assert list(rendered) == required
        codes = cast("list[str]", rendered["reason_codes"])
        assert set(codes) <= set(REASON_ENUM)
        assert rendered["n"] == cell.n
        assert cast("dict[str, str]", rendered["key"])
        if rendered["availability"] == MEASURED:
            assert isinstance(rendered["verdict"], str)
            assert isinstance(rendered["score"], float)
            assert cell.n >= 1
            assert codes == []
        else:
            assert (rendered["verdict"], rendered["score"]) == (None, None)
            assert codes


def test_module_constants_are_the_contract_spellings() -> None:
    assert [MEASURED, NOT_MEASURED] == DEFS["availability_state"]["enum"]
    assert list(MEASURED_VERDICTS) == DEFS["measured_verdict"]["enum"]
    assert list(REASON_CODES) == REASON_ENUM
    assert {REASON_CELL_BELOW_MIN_N, REASON_NO_OBSERVATIONS, SIGNAL_NOT_MEASURED} <= set(
        REASON_ENUM
    )
    assert module.__doc__ is not None


# --- the three cells of the card's scenario ---


def test_two_adequate_cells_and_one_sparse_cell_are_all_present_in_key_order() -> None:
    counter = _Counter()
    en, ja, ko = stratify(_cases(), ["locale"], 3, counter)
    assert [en.key, ja.key, ko.key] == [
        (("locale", "en"),),
        (("locale", "ja"),),
        (("locale", "ko"),),
    ]
    assert _parts(en) == (3, MEASURED, "pass", 2 / 3, ())
    assert _parts(ko) == (3, MEASURED, "pass", 2 / 3, ())
    assert _parts(ja) == (1, NOT_MEASURED, None, None, (REASON_CELL_BELOW_MIN_N,))
    assert [tuple(c.case_id for c in members) for members in counter.calls] == [
        ("e1", "e2", "e3"),
        ("k1", "k2", "k3"),
    ]


def test_the_floor_is_inclusive_on_both_sides() -> None:
    cases = [_case("a", "x"), _case("b", "x"), _case("c", "y")]
    at_floor, below = stratify(cases, ["locale"], 2, _mean_pass)
    assert (at_floor.key, _parts(at_floor)) == ((("locale", "x"),), (2, MEASURED, "pass", 1.0, ()))
    assert (below.key, _parts(below)) == (
        (("locale", "y"),),
        (1, NOT_MEASURED, None, None, (REASON_CELL_BELOW_MIN_N,)),
    )
    (only,) = stratify(cases[:1], ["locale"], 1, _mean_pass)
    assert _parts(only) == (1, MEASURED, "pass", 1.0, ())


def test_a_sparse_cell_never_reaches_the_measurement() -> None:
    counter = _Counter()
    (cell,) = stratify([_case("a", "x")], ["locale"], 2, counter)
    assert counter.calls == []
    assert _parts(cell) == (1, NOT_MEASURED, None, None, (REASON_CELL_BELOW_MIN_N,))


def test_a_requested_key_with_no_cases_is_reported_as_no_observations() -> None:
    counter = _Counter()
    fr, ko = stratify(_cases()[:3], ["locale"], 2, counter, requested=[{"locale": "fr"}])
    assert [fr.key, ko.key] == [(("locale", "fr"),), (("locale", "ko"),)]
    assert _parts(fr) == (0, NOT_MEASURED, None, None, (REASON_NO_OBSERVATIONS,))
    assert _parts(ko)[:2] == (3, MEASURED)
    assert len(counter.calls) == 1


def test_a_requested_key_that_has_cases_is_not_duplicated() -> None:
    cells = stratify(_cases()[:3], ["locale"], 2, _mean_pass, requested=[{"locale": "ko"}])
    assert [(cell.key, cell.n) for cell in cells] == [((("locale", "ko"),), 3)]


def test_no_cases_and_nothing_requested_is_an_empty_tuple() -> None:
    assert stratify([], ["locale"], 1, _mean_pass) == ()


def test_a_measurement_that_cannot_measure_is_reported_with_its_reasons() -> None:
    def unmeasurable(members: tuple[Case, ...]) -> Outcome:
        return (NOT_MEASURED, (SIGNAL_NOT_MEASURED, REASON_NO_OBSERVATIONS))

    (cell,) = stratify(_cases()[:3], ["locale"], 2, unmeasurable)
    assert _parts(cell) == (
        3,
        NOT_MEASURED,
        None,
        None,
        (SIGNAL_NOT_MEASURED, REASON_NO_OBSERVATIONS),
    )
    assert project(cell)["reason_codes"] == [SIGNAL_NOT_MEASURED, REASON_NO_OBSERVATIONS]


@pytest.mark.parametrize("code", [c for c in REASON_ENUM if c != REASON_CELL_BELOW_MIN_N])
def test_every_contract_reason_but_the_floor_passes_through_a_measurement(code: str) -> None:
    (cell,) = stratify(_cases()[:3], ["locale"], 2, lambda _members: (NOT_MEASURED, (code,)))
    assert _parts(cell) == (3, NOT_MEASURED, None, None, (code,))


@pytest.mark.parametrize(
    ("reasons", "detail"),
    [
        (("looks_fine",), "('looks_fine',)"),
        ((REASON_CELL_BELOW_MIN_N,), "('cell_below_min_n',)"),
        (
            (SIGNAL_NOT_MEASURED, REASON_CELL_BELOW_MIN_N),
            "('signal_not_measured', 'cell_below_min_n')",
        ),
    ],
)
def test_a_measurement_reason_outside_the_contract_or_claiming_the_floor_fails_closed(
    reasons: tuple[str, ...], detail: str
) -> None:
    """read.v1 refuses a foreign code, and a cell that reached the floor is not below it."""
    with pytest.raises(StratifyError) as error:
        stratify(_cases()[:3], ["locale"], 2, lambda _members: (NOT_MEASURED, reasons))
    assert str(error.value) == f"INVALID_MEASURE: {detail}"


def test_a_one_shot_iterable_of_cases_keeps_its_whole_population() -> None:
    expected = stratify(_cases(), ["locale"], 3, _mean_pass)
    one_shot = cast("Sequence[Case]", iter(_cases()))
    assert stratify(one_shot, ["locale"], 3, _mean_pass) == expected
    assert sum(cell.n for cell in expected) == len(_cases())
    grouped = group(cast("Sequence[Case]", iter(_cases())), ["locale"])
    assert grouped == group(_cases(), ["locale"])


# --- order and identity ---


def test_order_is_lexicographic_on_values_not_on_input_order() -> None:
    cases = [_case("a", "9"), _case("b", "10"), _case("c", "2")]
    assert [cell.key[0][1] for cell in stratify(cases, ["locale"], 1, _mean_pass)] == [
        "10",
        "2",
        "9",
    ]


def test_multi_axis_keys_follow_the_axes_order_given() -> None:
    cases = [_case("a", "ko", 1, model="m2"), _case("b", "en", 1, model="m1")]
    cells = stratify(cases, ["model", "locale"], 1, _mean_pass)
    assert [cell.key for cell in cells] == [
        (("model", "m1"), ("locale", "en")),
        (("model", "m2"), ("locale", "ko")),
    ]
    assert [list(cast("dict[str, str]", project(cell)["key"]).items()) for cell in cells] == [
        [("model", "m1"), ("locale", "en")],
        [("model", "m2"), ("locale", "ko")],
    ]


def test_extra_conditions_do_not_split_a_cell() -> None:
    cases = [_case("a", "ko", 1, model="m1"), _case("b", "ko", 0, model="m2")]
    (cell,) = stratify(cases, ["locale"], 2, _mean_pass)
    assert _parts(cell) == (2, MEASURED, "warn", 0.5, ())


def test_reordering_and_appending_leaves_existing_cells_byte_identical() -> None:
    before = [
        json.dumps(project(c), sort_keys=True)
        for c in stratify(_cases(), ["locale"], 3, _mean_pass)
    ]
    shuffled = [*reversed(_cases()), _case("f1", "fr")]
    after = {
        cast("dict[str, str]", project(c)["key"])["locale"]: json.dumps(project(c), sort_keys=True)
        for c in stratify(shuffled, ["locale"], 3, _mean_pass)
    }
    assert [after["en"], after["ja"], after["ko"]] == before
    assert json.loads(after["fr"]) == {
        "key": {"locale": "fr"},
        "n": 1,
        "availability": NOT_MEASURED,
        "verdict": None,
        "score": None,
        "reason_codes": [REASON_CELL_BELOW_MIN_N],
    }


def test_the_callers_input_is_neither_mutated_nor_reordered() -> None:
    cases = _cases()
    requested = [{"locale": "fr"}, {"locale": "de"}]
    snapshot = (copy.deepcopy(cases), copy.deepcopy(requested))
    identities = [id(case) for case in cases]
    stratify(cases, ["locale"], 3, _mean_pass, requested=requested)
    assert (cases, requested) == snapshot
    assert [id(case) for case in cases] == identities


def test_records_are_frozen() -> None:
    case = _case("a", "x")
    with pytest.raises(FrozenInstanceError):
        case.case_id = "b"  # type: ignore[misc]
    cell = stratify([case], ["locale"], 1, _mean_pass)[0]
    with pytest.raises(FrozenInstanceError):
        cell.n = 5  # type: ignore[misc]


# --- fail closed ---


@pytest.mark.parametrize(
    ("case_id", "conditions", "message"),
    [
        ("", {"locale": "ko"}, "INVALID_CASE_ID: ''"),
        (7, {"locale": "ko"}, "INVALID_CASE_ID: 7"),
        ("a", {"": "ko"}, "INVALID_CONDITION_KEY: ''"),
        ("a", {3: "ko"}, "INVALID_CONDITION_KEY: 3"),
        ("a", {"locale": 1}, "INVALID_CONDITION_VALUE: locale=1"),
        ("a", {"locale": None}, "INVALID_CONDITION_VALUE: locale=None"),
    ],
)
def test_make_case_fails_closed_naming_the_offending_input(
    case_id: object, conditions: object, message: str
) -> None:
    with pytest.raises(StratifyError) as error:
        make_case(cast("str", case_id), cast("dict[str, str]", conditions), None)
    assert (error.value.code, str(error.value)) == (message.split(":", 1)[0], message)


def test_make_case_sorts_condition_pairs_and_keeps_empty_values() -> None:
    case = make_case("a", {"model": "", "locale": "ko"}, {"raw": 1})
    assert case.conditions == (("locale", "ko"), ("model", ""))
    assert case.payload == {"raw": 1}


@pytest.mark.parametrize(
    ("axes", "detail"),
    [
        ([], "()"),
        (["locale", "locale"], "('locale', 'locale')"),
        ([""], "''"),
        (["locale", cast("str", 3)], "3"),
        ("locale", "'locale'"),
        ("model", "'model'"),
    ],
)
def test_bad_axes_fail_closed_naming_the_offending_input(axes: Sequence[str], detail: str) -> None:
    with pytest.raises(StratifyError) as error:
        stratify(_cases(), axes, 1, _mean_pass)
    assert (error.value.code, str(error.value)) == ("INVALID_AXES", f"INVALID_AXES: {detail}")
    with pytest.raises(StratifyError) as error:
        group(_cases(), axes)
    assert str(error.value) == f"INVALID_AXES: {detail}"


@pytest.mark.parametrize(
    ("floor", "detail"), [(0, "0"), (-1, "-1"), (True, "True"), (2.0, "2.0"), (None, "None")]
)
def test_bad_min_cell_n_fails_closed_naming_the_value(floor: object, detail: str) -> None:
    with pytest.raises(StratifyError) as error:
        stratify(_cases(), ["locale"], cast("int", floor), _mean_pass)
    assert str(error.value) == f"INVALID_MIN_CELL_N: {detail}"


def test_a_case_missing_an_axis_fails_closed_not_bucketed() -> None:
    cases = [_case("a", "ko"), make_case("b", {"model": "m"}, 1)]
    with pytest.raises(StratifyError) as error:
        stratify(cases, ["locale"], 1, _mean_pass)
    assert error.value.code == "MISSING_AXIS"
    assert str(error.value) == "MISSING_AXIS: b:locale"


def test_a_non_string_value_smuggled_past_make_case_fails_closed() -> None:
    forged = replace(_case("a", "ko"), conditions=(("locale", cast("str", 3)),))
    with pytest.raises(StratifyError) as error:
        stratify([forged], ["locale"], 1, _mean_pass)
    assert error.value.code == "INVALID_CONDITION_VALUE"
    assert str(error.value) == "INVALID_CONDITION_VALUE: a:locale=3"


def test_duplicate_case_ids_and_foreign_objects_fail_closed() -> None:
    with pytest.raises(StratifyError) as error:
        group([_case("a", "ko"), _case("a", "en")], ["locale"])
    assert (error.value.code, str(error.value)) == ("DUPLICATE_CASE", "DUPLICATE_CASE: a")
    with pytest.raises(StratifyError) as error:
        stratify([_case("a", "ko"), _case("a", "ko")], ["locale"], 1, _mean_pass)
    assert error.value.code == "DUPLICATE_CASE"
    with pytest.raises(StratifyError) as error:
        group([_case("a", "ko"), cast("Case", "not a case")], ["locale"])
    assert str(error.value) == "INVALID_CASE: 'not a case'"
    with pytest.raises(StratifyError) as error:
        stratify([cast("Case", ("a", "ko"))], ["locale"], 1, _mean_pass)
    assert str(error.value) == "INVALID_CASE: ('a', 'ko')"


@pytest.mark.parametrize(
    ("requested", "detail"),
    [
        ([{"locale": "ko", "model": "m"}], "{'locale': 'ko', 'model': 'm'}"),
        ([{"model": "m"}], "{'model': 'm'}"),
        ([dict[str, str]()], "{}"),
        ([{"locale": "ko"}, {"region": "eu"}], "{'region': 'eu'}"),
    ],
)
def test_a_requested_key_must_name_exactly_the_axes(
    requested: list[dict[str, str]], detail: str
) -> None:
    with pytest.raises(StratifyError) as error:
        stratify(_cases(), ["locale"], 1, _mean_pass, requested=requested)
    assert str(error.value) == f"INVALID_REQUESTED_KEY: {detail}"


def test_a_requested_key_with_a_non_string_value_fails_closed() -> None:
    with pytest.raises(StratifyError) as error:
        stratify(
            _cases(), ["locale"], 1, _mean_pass, requested=[cast("dict[str, str]", {"locale": 1})]
        )
    assert str(error.value) == "INVALID_CONDITION_VALUE: locale=1"


class _Verdict(str):
    """A str subclass: equal to a verdict, but not the exact type a read carries."""

    __slots__ = ()


@pytest.mark.parametrize(
    ("result", "detail"),
    [
        (("pass",), "('pass',)"),  # the whole result is named when its shape is wrong
        (("pass", 0.5, 1), "('pass', 0.5, 1)"),
        (["pass", 0.5], "['pass', 0.5]"),
        (None, "None"),
        (("ok", 0.5), "'ok'"),  # the verdict is named when it is not a measured verdict
        ((3, 0.5), "3"),
        ((_Verdict("pass"), 0.5), "'pass'"),
        (("pass", None), "None"),  # the score is named when it is not a number in [0, 1]
        (("pass", True), "True"),
        (("pass", "0.5"), "'0.5'"),
        (("pass", -0.1), "-0.1"),
        (("pass", 1.1), "1.1"),
        (("pass", 2), "2"),
        (("pass", 10**400), repr(10**400)),
        (("pass", float("nan")), "nan"),
        (("pass", float("inf")), "inf"),
        (("pass", float("-inf")), "-inf"),
        ((NOT_MEASURED, ()), "()"),  # the reasons are named when they are not valid codes
        ((NOT_MEASURED, None), "None"),
        ((NOT_MEASURED, [SIGNAL_NOT_MEASURED]), "['signal_not_measured']"),
        ((NOT_MEASURED, ("",)), "('',)"),
        ((NOT_MEASURED, (3,)), "(3,)"),
        (
            (NOT_MEASURED, (SIGNAL_NOT_MEASURED, SIGNAL_NOT_MEASURED)),
            "('signal_not_measured', 'signal_not_measured')",
        ),
        ((NOT_MEASURED, (SIGNAL_NOT_MEASURED, 3)), "('signal_not_measured', 3)"),
        ((NOT_MEASURED, 0.5), "0.5"),
    ],
)
def test_an_ill_formed_measurement_fails_closed_naming_what_is_wrong(
    result: object, detail: str
) -> None:
    with pytest.raises(StratifyError) as error:
        stratify(_cases()[:3], ["locale"], 1, lambda _members: cast("Outcome", result))
    assert (error.value.code, str(error.value)) == (
        "INVALID_MEASURE",
        f"INVALID_MEASURE: {detail}",
    )


def test_a_measured_cell_keeps_the_verdict_and_score_in_their_places() -> None:
    (cell,) = stratify(_cases()[:3], ["locale"], 1, lambda _members: ("warn", 0.25))
    assert (cell.verdict, cell.score) == ("warn", 0.25)
    (reasons,) = stratify(
        _cases()[:3], ["locale"], 1, lambda _members: (NOT_MEASURED, (SIGNAL_NOT_MEASURED,))
    )
    assert (reasons.verdict, reasons.score, reasons.reason_codes) == (
        None,
        None,
        (SIGNAL_NOT_MEASURED,),
    )


def test_measurement_scores_are_floats_at_both_ends() -> None:
    zero, one = (
        stratify([_case("a", "x")], ["locale"], 1, lambda _m: ("fail", 0))[0],
        stratify([_case("a", "x")], ["locale"], 1, lambda _m: ("pass", 1))[0],
    )
    assert (zero.verdict, zero.score, type(zero.score)) == ("fail", 0.0, float)
    assert (one.verdict, one.score, type(one.score)) == ("pass", 1.0, float)


# --- grouping helper ---


def test_group_keeps_members_in_input_order_under_sorted_keys() -> None:
    grouped = group(_cases(), ["locale"])
    assert list(grouped) == [(("locale", "en"),), (("locale", "ja"),), (("locale", "ko"),)]
    assert [c.case_id for c in grouped[(("locale", "ko"),)]] == ["k1", "k2", "k3"]
    assert group([], ["locale"]) == {}


# --- properties ---


@settings(deadline=None, max_examples=60)
@given(
    st.lists(
        st.tuples(st.sampled_from(["a", "b", "c", "d"]), st.integers(0, 1)),
        min_size=0,
        max_size=12,
    ),
    st.integers(1, 4),
)
def test_every_case_lands_in_exactly_one_cell_and_shuffling_changes_nothing(
    rows: list[tuple[str, int]], floor: int
) -> None:
    cases = [
        make_case(f"c{i}", {"locale": value}, payload) for i, (value, payload) in enumerate(rows)
    ]
    cells = stratify(cases, ["locale"], floor, _mean_pass)
    assert sum(cell.n for cell in cells) == len(cases)
    assert {cell.key[0][1] for cell in cells} == {value for value, _payload in rows}
    assert [cell.key for cell in cells] == sorted(cell.key for cell in cells)
    for cell in cells:
        expected = (MEASURED, ()) if cell.n >= floor else (NOT_MEASURED, (REASON_CELL_BELOW_MIN_N,))
        assert (cell.availability, cell.reason_codes) == expected
        assert (cell.verdict is None) == (cell.availability == NOT_MEASURED)
    reversed_cells = stratify(list(reversed(cases)), ["locale"], floor, _mean_pass)
    assert [project(c) for c in reversed_cells] == [project(c) for c in cells]


def test_cell_is_a_plain_record_of_its_fields() -> None:
    cell = Cell((("locale", "ko"),), 2, MEASURED, "warn", 0.5, ())
    assert project(cell) == {
        "key": {"locale": "ko"},
        "n": 2,
        "availability": "measured",
        "verdict": "warn",
        "score": 0.5,
        "reason_codes": [],
    }
