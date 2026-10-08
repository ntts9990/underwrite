"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from itertools import permutations
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from hypothesis import given, settings
from hypothesis import strategies as st

from underwrite.measurement import availability as av
from underwrite.measurement.availability import (
    AvailabilityInputError,
    assess,
    cap,
    make_panel_read,
    make_policy,
    project,
    project_capped,
)

ROOT = repo_root(Path(__file__))
READ_SCHEMA = ROOT / "contracts/read.v1.schema.json"
POLICY_SCHEMA = ROOT / "contracts/measurement_policy.v1.schema.json"

ABC = ("A", "B", "C")


def _schema() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(READ_SCHEMA.read_text(encoding="utf-8")))


def _defs() -> dict[str, Any]:
    return cast("dict[str, Any]", _schema()["$defs"])


def _policy(quorum: int = 2, required: tuple[str, ...] = ABC) -> av.QuorumPolicy:
    return make_policy(required, quorum)


def _read(verdict: str = "pass", score: float = 0.8125, escalate: bool = False) -> av.PanelRead:
    return make_panel_read(verdict, score, escalate)


# --- the three states, one branch each ---


def test_full_panel_leaves_the_read_alone_and_records_triggered_false() -> None:
    result = assess(_policy(), ["C", "A", "B"], {})
    assert result.state == "full"
    assert result.triggered is False
    assert result.available == ABC
    assert result.missing == ()
    assert result.losses == ()
    capped = cap(_read("pass", 0.8125, False), result)
    assert capped == av.CappedRead("pass", 0.8125, False, ())
    kept = cap(_read("fail", 0.25, True), result)
    assert kept == av.CappedRead("fail", 0.25, True, ())


def test_partial_loss_above_quorum_keeps_the_score_bits_and_lowers_only_pass() -> None:
    result = assess(_policy(), ["B", "A"], {})
    assert result.state == "partial"
    assert result.triggered is True
    assert result.available == ("A", "B")
    assert result.missing == ("C",)
    assert result.losses == (("C", "missing"),)
    read = _read("pass", 0.8125, False)
    capped = cap(read, result)
    assert capped.verdict == "warn"
    assert capped.score == read.score
    assert capped.score is not None
    assert capped.score.hex() == read.score.hex()
    assert capped.escalate is True
    assert capped.reason_codes == ("required_instrument_missing",)
    assert cap(_read("warn", 0.5, False), result).verdict == "warn"
    assert cap(_read("fail", 0.5, False), result).verdict == "fail"


def test_below_quorum_is_no_read_at_all() -> None:
    result = assess(_policy(), ["A"], {})
    assert result.state == "below_quorum"
    assert result.triggered is True
    assert result.available == ("A",)
    assert result.missing == ("B", "C")
    capped = cap(_read("pass", 0.8125, False), result)
    assert capped.verdict == "not_measured"
    assert capped.score is None
    assert capped.escalate is True
    assert capped.reason_codes == ("below_quorum", "required_instrument_missing")


def test_the_quorum_boundary_falls_on_the_partial_side() -> None:
    policy = _policy(quorum=2)
    assert assess(policy, ["A", "B"], {}).state == "partial"
    assert assess(policy, ["A"], {}).state == "below_quorum"
    assert assess(policy, ["A", "B", "C"], {}).state == "full"
    everything = make_policy(ABC, 3)
    assert assess(everything, ["A", "B"], {}).state == "below_quorum"
    assert assess(everything, ABC, {}).state == "full"
    one = make_policy(ABC, 1)
    assert assess(one, ["C"], {}).state == "partial"
    assert assess(one, [], {}).state == "below_quorum"


def test_known_losses_are_told_apart_and_unexplained_absence_is_missing() -> None:
    result = assess(_policy(), ["A"], {"B": "error"})
    assert result.losses == (("B", "error"), ("C", "missing"))
    capped = cap(_read(), result)
    assert capped.reason_codes == (
        "below_quorum",
        "required_instrument_error",
        "required_instrument_missing",
    )
    partial = cap(_read(), assess(_policy(), ["A", "B"], {"C": "error"}))
    assert partial.reason_codes == ("required_instrument_error",)


# --- order invariance and exhaustiveness ---


def test_any_order_or_repetition_of_the_reported_list_gives_one_result() -> None:
    policy = _policy()
    baseline = assess(policy, ["A", "B"], {})
    for order in permutations(["B", "A", "B", "A"]):
        assert assess(policy, list(order), {}) == baseline
    assert make_policy(("C", "B", "A"), 2) == policy


@settings(deadline=None, max_examples=200)
@given(
    reported=st.lists(st.sampled_from(ABC), max_size=6),
    quorum=st.integers(min_value=1, max_value=3),
    verdict=st.sampled_from(("pass", "warn", "fail")),
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    escalate=st.booleans(),
)
def test_the_state_matrix_is_exhaustive_exclusive_and_consistent(
    reported: list[str], quorum: int, verdict: str, score: float, escalate: bool
) -> None:
    policy = _policy(quorum)
    result = assess(policy, reported, {})
    distinct = sorted(set(reported))
    assert list(result.available) == distinct
    assert set(result.available) | set(result.missing) == set(ABC)
    assert not set(result.available) & set(result.missing)
    expected = (
        "full" if not result.missing else "partial" if len(distinct) >= quorum else "below_quorum"
    )
    assert result.state == expected
    assert result.triggered is (expected != "full")
    capped = cap(_read(verdict, score, escalate), result)
    if expected == "below_quorum":
        assert (capped.verdict, capped.score) == ("not_measured", None)
        assert "below_quorum" in capped.reason_codes
    else:
        assert capped.score is not None
        assert capped.score.hex() == float(score).hex()
        assert "below_quorum" not in capped.reason_codes
        if expected == "full":
            assert (capped.verdict, capped.escalate) == (verdict, escalate)
        else:
            assert capped.verdict == ("warn" if verdict == "pass" else verdict)
            assert capped.escalate is True


# --- refused inputs, each by its code and a detail naming the offending input ---


@pytest.mark.parametrize(
    ("required", "quorum", "code", "detail"),
    [
        (("A", "A"), 1, "DUPLICATE_INSTRUMENT", "('A', 'A')"),
        (("A", ""), 1, "INVALID_INSTRUMENT", "''"),
        (("A", 3), 1, "INVALID_INSTRUMENT", "3"),
        (ABC, 0, "INVALID_QUORUM", "0"),
        (ABC, True, "INVALID_QUORUM", "True"),
        (ABC, 2.0, "INVALID_QUORUM", "2.0"),
        (ABC, 4, "QUORUM_UNREACHABLE", "4 > 3"),
        ((), 1, "QUORUM_UNREACHABLE", "1 > 0"),
    ],
)
def test_a_malformed_policy_is_refused(
    required: tuple[Any, ...], quorum: Any, code: str, detail: str
) -> None:
    with pytest.raises(AvailabilityInputError) as caught:
        make_policy(required, quorum)
    assert (caught.value.code, caught.value.detail) == (code, detail)


@pytest.mark.parametrize(
    ("verdict", "score", "escalate", "code", "detail"),
    [
        ("not_measured", 0.5, False, "INVALID_VERDICT", "'not_measured'"),
        ("ok", 0.5, False, "INVALID_VERDICT", "'ok'"),
        ("pass", 1.5, False, "INVALID_SCORE", "1.5"),
        ("pass", -0.1, False, "INVALID_SCORE", "-0.1"),
        ("pass", float("nan"), False, "INVALID_SCORE", "nan"),
        ("pass", float("inf"), False, "INVALID_SCORE", "inf"),
        ("pass", True, False, "INVALID_SCORE", "True"),
        ("pass", "0.5", False, "INVALID_SCORE", "'0.5'"),
        ("pass", 0.5, 1, "INVALID_ESCALATE", "1"),
    ],
)
def test_a_malformed_panel_read_is_refused(
    verdict: Any, score: Any, escalate: Any, code: str, detail: str
) -> None:
    with pytest.raises(AvailabilityInputError) as caught:
        make_panel_read(verdict, score, escalate)
    assert (caught.value.code, caught.value.detail) == (code, detail)


def test_score_bounds_are_inclusive_and_integers_become_floats() -> None:
    assert make_panel_read("pass", 0, False).score == 0.0
    assert make_panel_read("pass", 1, True).score == 1.0
    assert isinstance(make_panel_read("warn", 1, True).score, float)


@pytest.mark.parametrize(
    ("reported", "losses", "code", "detail"),
    [
        (["A", "Z"], {}, "UNKNOWN_INSTRUMENT", "Z"),
        (["A"], {"Z": "missing"}, "UNKNOWN_INSTRUMENT", "Z"),
        (["A"], {"B": "absent"}, "UNKNOWN_LOSS", "B: 'absent'"),
        (["A"], {"A": "error"}, "CONTRADICTORY_LOSS", "A"),
        ([""], {}, "INVALID_INSTRUMENT", "''"),
        (["A"], {3: "error"}, "INVALID_INSTRUMENT", "3"),
    ],
)
def test_a_malformed_panel_is_refused(
    reported: list[Any], losses: dict[Any, Any], code: str, detail: str
) -> None:
    with pytest.raises(AvailabilityInputError) as caught:
        assess(_policy(), reported, losses)
    assert (caught.value.code, caught.value.detail) == (code, detail)


def test_the_error_carries_its_code_and_detail() -> None:
    error = AvailabilityInputError("INVALID_QUORUM", "0")
    assert (error.code, error.detail, str(error)) == ("INVALID_QUORUM", "0", "INVALID_QUORUM: 0")
    assert str(AvailabilityInputError("INVALID_QUORUM")) == "INVALID_QUORUM"


# --- contract fit ---


def test_projection_has_exactly_the_contract_keys_and_spellings() -> None:
    defs = _defs()
    required = cast("list[str]", defs["availability"]["required"])
    for reported in (list(ABC), ["A", "B"], ["A"]):
        rendered = project(assess(_policy(), reported, {}))
        assert list(rendered) == required
        assert rendered["state"] in defs["availability"]["properties"]["state"]["enum"]
        assert rendered["required_instruments"] == list(ABC)
    assert project(assess(_policy(), ["B", "A"], {})) == {
        "required_instruments": ["A", "B", "C"],
        "quorum": 2,
        "available": ["A", "B"],
        "missing": ["C"],
        "state": "partial",
        "triggered": True,
    }
    codes = set(cast("list[str]", defs["reason_codes"]["items"]["enum"]))
    for reported in (["A"], ["A", "B"]):
        capped = project_capped(cap(_read(), assess(_policy(), reported, {"C": "error"})))
        assert list(capped) == ["verdict", "score", "escalate", "reason_codes"]
        assert set(capped) <= set(cast("list[str]", _schema()["required"]))
        assert set(cast("list[str]", capped["reason_codes"])) <= codes
    below = project_capped(cap(_read(), assess(_policy(), ["A"], {})))
    assert below["verdict"] == defs["not_measured_read"]["properties"]["verdict"]["const"]
    assert below["score"] is None
    assert project_capped(cap(_read("pass", 0.5, False), assess(_policy(), ABC, {}))) == {
        "verdict": "pass",
        "score": 0.5,
        "escalate": False,
        "reason_codes": [],
    }


def test_policy_inputs_are_the_contract_policy_inputs() -> None:
    policy = cast("dict[str, Any]", json.loads(POLICY_SCHEMA.read_text(encoding="utf-8")))
    assert {"required_instruments", "quorum"} <= set(cast("list[str]", policy["required"]))
    assert policy["properties"]["quorum"]["minimum"] == 1
    defs = _defs()
    assert list(av.MEASURED_VERDICTS) == defs["measured_verdict"]["enum"]
    assert av.NOT_MEASURED == defs["not_measured_read"]["properties"]["verdict"]["const"]
    # Every loss kind renders a registered reason, and every registered per-instrument
    # reason is reachable from some loss kind -- the contract owns the list, not this test.
    codes = cast("list[str]", defs["reason_codes"]["items"]["enum"])
    rendered = {
        loss: cap(_read(), assess(_policy(), ["A", "B"], {"C": loss})).reason_codes
        for loss in av.LOSSES
    }
    assert all(len(reasons) == 1 for reasons in rendered.values())
    assert {reasons[0] for reasons in rendered.values()} == {
        code for code in codes if code.startswith("required_instrument_")
    }
