"""Standalone behavior and boundary checks."""

from __future__ import annotations

import hashlib
import inspect
import math
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, asdict
from decimal import Decimal
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from underwrite.instrument.evidence import split
from underwrite.instrument.evidence.split import SplitAssignment, SplitInputError, assign_split


class CustomString(str):
    pass


class CustomFloat(float):
    pass


class CustomInt(int):
    pass


class CustomList(list[str]):
    pass


class CustomTuple(tuple[str, ...]):
    pass


def test_append_and_reorder_do_not_repeat_positional_reshuffle() -> None:
    initial = ["C", "A", "B"]
    old = assign_split(initial, salt="stable", confirm_fraction=0.5)
    new = assign_split(["D", *reversed(initial)], salt="stable", confirm_fraction=0.5)
    assert old.assignments == tuple(item for item in new.assignments if item.case_id != "D")
    assert tuple(item.case_id for item in old.assignments) == ("A", "B", "C")
    assert initial == ["C", "A", "B"]
    # A cardinality-based positional cut actually reshuffles B on this same growth.
    assert sorted(initial).index("B") >= len(initial) // 2
    assert sorted([*initial, "D"]).index("B") < (len(initial) + 1) // 2


@given(st.lists(st.text(alphabet="ABCdef0123|-é한", min_size=1, max_size=12), unique=True))
def test_partitions_are_sorted_disjoint_exhaustive_and_append_stable(ids: list[str]) -> None:
    result = assign_split(ids, salt="population-v1", confirm_fraction=0.3)
    assert result == assign_split(tuple(reversed(ids)), salt="population-v1", confirm_fraction=0.3)
    assert tuple(item.case_id for item in result.assignments) == tuple(sorted(ids))
    assert set(result.explore_case_ids).isdisjoint(result.confirm_case_ids)
    assert set(result.explore_case_ids) | set(result.confirm_case_ids) == set(ids)
    assert result.n_cases == len(ids) == result.explore_count + result.confirm_count
    assert result.explore_count == len(result.explore_case_ids)
    assert result.confirm_count == len(result.confirm_case_ids)
    extended = assign_split([*ids, "new-case"], salt="population-v1", confirm_fraction=0.3)
    assert result.assignments == tuple(a for a in extended.assignments if a.case_id != "new-case")


def test_empty_partition_and_one_sided_partition_do_not_claim_adequacy() -> None:
    result = assign_split([], salt="", confirm_fraction=0.5)
    assert result.assignments == result.explore_case_ids == result.confirm_case_ids == ()
    assert result.n_cases == result.explore_count == result.confirm_count == 0
    assert result.reason == "empty_population"
    one = assign_split(["A"], salt="", confirm_fraction=0.5)
    assert one.n_cases == 1
    assert one.reason is None
    assert 0 in (one.explore_count, one.confirm_count)
    assert (
        result.limitations
        == one.limitations
        == (
            "delimiter_encoding_can_collide_across_salt_case_pairs",
            "salt_context_requires_external_governance",
            "confirm_secrecy_and_selection_not_enforced",
            "holdout_adequacy_not_established",
        )
    )


def test_owned_frozen_records_and_metadata() -> None:
    ids = ["B", "A"]
    fraction = 0.3
    result = assign_split(ids, salt="recorded", confirm_fraction=fraction)
    before = asdict(result)
    ids[:] = ["replaced"]
    assert asdict(result) == before
    assert result.method == "sha256-delimited-uint64-float-v1"
    assert result.salt == "recorded"
    assert result.confirm_fraction == fraction
    assert type(result.assignments) is tuple
    assert type(result.limitations) is tuple
    assert all(type(item) is SplitAssignment for item in result.assignments)
    for record, attribute in ((result, "salt"), (result.assignments[0], "split")):
        with pytest.raises(FrozenInstanceError):
            setattr(record, attribute, "changed")


@pytest.mark.parametrize(
    "case_id,salt", [("case-002", "sample-evalset-v1"), ("한é", ""), ("A", "s")]
)
def test_actual_hash_boundary_uses_float_and_strict_less_than(case_id: str, salt: str) -> None:
    first_eight_hex = hashlib.sha256(f"{salt}|{case_id}".encode()).hexdigest()[:16]
    threshold = int(first_eight_hex, 16) / float(2**64)
    for ratio, expected in (
        (math.nextafter(threshold, 0.0), "explore"),
        (threshold, "explore"),
        (math.nextafter(threshold, 1.0), "confirm"),
    ):
        result = assign_split([case_id], salt=salt, confirm_fraction=ratio)
        assert len(result.assignments) == 1
        assert result.assignments[0].case_id == case_id
        assert result.assignments[0].split == expected


def test_each_named_partition_holds_the_cases_the_hash_rule_assigns_to_it() -> None:
    """Which name carries which assignment, against the pinned rule rather than the code.

    Disjointness and exhaustiveness hold of any two-way cut, so they cannot say that
    ``confirm_case_ids`` is the confirm side. Confirm is the holdout the rest of the
    pipeline is meant not to see, so the name has to be checked, not inferred.
    """
    ids = ["case-a", "case-b", "case-c", "case-d", "case-e"]
    salt, fraction = "named-partitions", 0.5
    expected = {
        case_id: int(hashlib.sha256(f"{salt}|{case_id}".encode()).hexdigest()[:16], 16)
        / float(2**64)
        < fraction
        for case_id in ids
    }

    result = assign_split(ids, salt=salt, confirm_fraction=fraction)

    assert result.confirm_case_ids == tuple(i for i in sorted(ids) if expected[i])
    assert result.explore_case_ids == tuple(i for i in sorted(ids) if not expected[i])
    # Neither partition is empty here, so neither equality is satisfied by absence.
    assert result.confirm_case_ids and result.explore_case_ids


def test_delimiter_collision_is_preserved_and_disclosed_not_redesigned() -> None:
    left = assign_split(["c"], salt="a|b", confirm_fraction=0.3)
    right = assign_split(["b|c"], salt="a", confirm_fraction=0.3)
    assert left.assignments[0].split == right.assignments[0].split
    assert left.salt != right.salt
    assert left.assignments[0].case_id != right.assignments[0].case_id
    assert "delimiter_encoding_can_collide_across_salt_case_pairs" in left.limitations


def test_explicit_salt_and_fraction_changes_are_visible_not_forced_flips() -> None:
    base = assign_split(["A", "B"], salt="", confirm_fraction=0.2)
    other_salt = assign_split(["A", "B"], salt=" ", confirm_fraction=0.2)
    ratio = 0.8
    other_ratio = assign_split(["A", "B"], salt="", confirm_fraction=ratio)
    assert base.salt == ""
    assert other_salt.salt == " "
    assert other_ratio.confirm_fraction == ratio
    assert set(base.confirm_case_ids) <= set(other_ratio.confirm_case_ids)
    assert base != other_salt
    assert base != other_ratio


@pytest.mark.parametrize(
    "population", [None, "A", {"A"}, {"A": 1}, iter(["A"]), CustomList(["A"]), CustomTuple(["A"])]
)
def test_only_exact_list_and_tuple_populations(population: object) -> None:
    with pytest.raises(SplitInputError) as error:
        assign_split(cast(Sequence[str], population), salt="s", confirm_fraction=0.5)
    assert error.value.code == "INVALID_POPULATION"
    assert error.value.case_id is None


@pytest.mark.parametrize(
    "case_id", [None, True, 1, "", " \t\n", "e\u0301", "\ud800", CustomString("A")]
)
def test_invalid_case_ids_reject_atomically(
    case_id: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_hash(_value: bytes) -> None:
        pytest.fail("hash work began before all IDs were validated")

    monkeypatch.setattr(split, "sha256", forbidden_hash)
    with pytest.raises(SplitInputError) as error:
        assign_split(["valid-first", cast(str, case_id)], salt="s", confirm_fraction=0.5)
    assert error.value.code == "INVALID_CASE_ID"


@pytest.mark.parametrize("salt", [None, True, 3, "e\u0301", "\udfff", CustomString("s")])
def test_invalid_salt_rejected_even_for_empty_population(salt: object) -> None:
    with pytest.raises(SplitInputError) as error:
        assign_split([], salt=cast(str, salt), confirm_fraction=0.5)
    assert error.value.code == "INVALID_SALT"


@pytest.mark.parametrize(
    "ratio",
    [
        0,
        1,
        -1,
        2,
        0.0,
        1.0,
        -0.1,
        1.1,
        math.nan,
        math.inf,
        -math.inf,
        True,
        False,
        "0.5",
        None,
        Decimal("0.5"),
        CustomFloat(0.5),
        CustomInt(0),
        10**500,
    ],
)
def test_invalid_fraction_is_typed_not_coerced(ratio: object) -> None:
    with pytest.raises(SplitInputError) as error:
        assign_split([], salt="s", confirm_fraction=cast(float, ratio))
    assert error.value.code == "INVALID_FRACTION"


def test_duplicate_case_id_reports_identity_instead_of_deduplicating() -> None:
    with pytest.raises(SplitInputError) as error:
        assign_split(["A", "B", "A"], salt="s", confirm_fraction=0.5)
    assert error.value.code == "DUPLICATE_CASE_ID"
    assert error.value.case_id == "A"
    assert str(error.value) == "DUPLICATE_CASE_ID: 'A'"


def test_policy_is_explicit_and_split_has_no_rng_seed() -> None:
    parameters = inspect.signature(assign_split).parameters
    assert tuple(parameters) == ("case_ids", "salt", "confirm_fraction")
    assert all(p.default is inspect.Parameter.empty for p in parameters.values())
    assert parameters["salt"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["confirm_fraction"].kind is inspect.Parameter.KEYWORD_ONLY
