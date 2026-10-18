"""Current explicit-role input selection preserves identities, bounds and missingness."""

from __future__ import annotations

import copy
from typing import Any, NoReturn, cast

import pytest
from _event_acceptance import inputs
from underwrite_core.canonical import content_digest

from underwrite.measurement import event_read, read_input
from underwrite.measurement.read_input import ReadInputError, Slot


def _measure(observation: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    return event_read.assemble_event_read(
        observation,
        policy,
        observation_digest=content_digest(observation),
        policy_digest=content_digest(policy),
    )


def _reject(observation: dict[str, Any], policy: dict[str, Any], reason: str) -> None:
    with pytest.raises(ReadInputError) as caught:
        _measure(observation, policy)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_OBSERVATION",
        reason,
        "/observation",
    )


@pytest.fixture
def no_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("invalid selected input reached statistics")

    monkeypatch.setattr(event_read, "run_directional", fail)
    for name in ("brier", "ece", "pass_k"):
        monkeypatch.setattr(event_read.calibration, name, fail)


def test_selection_requires_an_explicit_value_decoder() -> None:
    with pytest.raises(TypeError, match="decode"):
        cast(Any, read_input.select_slots)({"readings": [], "availability": []}, {})


def test_explicit_decoder_controls_consumed_values_and_keeps_order_splits_and_losses() -> None:
    payload = {
        "readings": [
            {
                "instrument": "selected",
                "metric_id": "event",
                "rows": [
                    {
                        "case_id": "a",
                        "sequence": 0,
                        "repeat": 0,
                        "split": "explore",
                        "score": "unconsumed",
                    },
                    {"case_id": "b", "sequence": 1, "repeat": 0},
                ],
            }
        ],
        "availability": [
            {
                "instrument": "selected",
                "metric_id": "event",
                "case_id": "a",
                "repeat": 0,
                "availability": "observed",
                "split": "explore",
            },
            {
                "instrument": "selected",
                "metric_id": "event",
                "case_id": "c",
                "repeat": 0,
                "availability": "missing",
            },
            {
                "instrument": "selected",
                "metric_id": "event",
                "case_id": "d",
                "repeat": 0,
                "availability": "error",
            },
        ],
    }
    before = copy.deepcopy(payload)
    calls: list[tuple[str, str | None]] = []
    marker = Slot(1, None, None, None)

    def decode(row: read_input.JSON, state: str | None) -> Slot:
        calls.append((row["case_id"], state))
        return marker

    slots, splits, order = read_input.select_slots(
        payload,
        {"instrument": "selected", "metric_id": "event"},
        decode=decode,
    )
    assert calls == [("a", "observed"), ("b", None)]
    assert order == ["a", "b"] and splits == {"a": "explore"}
    assert slots == {
        "a": marker,
        "b": marker,
        "c": Slot(None, None, None, "missing"),
        "d": Slot(None, None, None, "error"),
    }
    assert payload == before


@pytest.mark.parametrize(
    "value", [None, "", 3, "e\u0301", "bad\x00id", "x" * (read_input.MAX_IDENTITY + 1)]
)
@pytest.mark.parametrize("location", ["/policy", "/observation"])
def test_current_identity_guard_preserves_finite_diagnostics(value: object, location: str) -> None:
    with pytest.raises(ReadInputError) as caught:
        read_input.identity(value, location)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_POLICY" if location == "/policy" else "INVALID_OBSERVATION",
        "INVALID_IDENTITY",
        location,
    )


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -float("inf"), -0.1, 1.1])
def test_selected_probability_guard_rejects_nonfinite_boolean_and_out_of_range(
    value: object,
) -> None:
    with pytest.raises(ReadInputError, match="INVALID_NUMBER"):
        read_input.number(value, 0, 1, "/observation")


@pytest.mark.parametrize("value", [True, 1.5, -1, 2**53, None])
def test_sequence_integer_guard_does_not_coerce(value: object) -> None:
    with pytest.raises(ReadInputError, match="INVALID_INTEGER"):
        read_input.integer(value, 0, 2**53 - 1, "/observation")


def test_current_guard_endpoints_and_normalized_identity_are_accepted() -> None:
    assert read_input.identity("é", "/observation") == "é"
    assert (
        read_input.identity("x" * read_input.MAX_IDENTITY, "/policy")
        == "x" * read_input.MAX_IDENTITY
    )
    for value in (0, 1):
        read_input.number(value, 0, 1, "/observation")
    for value in (0, 2**53 - 1):
        read_input.integer(value, 0, 2**53 - 1, "/observation")


@pytest.mark.usefixtures("no_statistics")
@pytest.mark.parametrize("field", ["readings", "availability", "schema_version"])
def test_missing_current_bundle_fields_refuse_before_computation(field: str) -> None:
    observation, policy = inputs([1, 0])
    del observation["payload"][field]
    _reject(observation, policy, "INVALID_BUNDLE_FIELDS")


@pytest.mark.usefixtures("no_statistics")
@pytest.mark.parametrize("field", ["readings", "availability"])
@pytest.mark.parametrize("value", [None, {}, [None]])
def test_malformed_current_bundle_containers_refuse_before_computation(
    field: str, value: object
) -> None:
    observation, policy = inputs([1, 0])
    observation["payload"][field] = value
    _reject(observation, policy, "INVALID_BUNDLE_FIELDS")


@pytest.mark.usefixtures("no_statistics")
@pytest.mark.parametrize("field", ["case_id", "sequence", "repeat", "outcome"])
def test_missing_selected_outcome_fields_refuse(field: str) -> None:
    observation, policy = inputs([1, 0])
    del observation["payload"]["readings"][0]["rows"][0][field]
    _reject(observation, policy, "INVALID_BUNDLE_FIELDS")


@pytest.mark.usefixtures("no_statistics")
@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("epoch", 0, "UNSUPPORTED_CASE_IDENTITY"),
        ("repeat", True, "UNSUPPORTED_CASE_IDENTITY"),
        ("repeat", 1, "UNSUPPORTED_CASE_IDENTITY"),
        ("sequence", 2**53, "INVALID_INTEGER"),
        ("sequence", True, "INVALID_INTEGER"),
    ],
)
def test_selected_row_metadata_keeps_its_specific_refusal(
    field: str, value: object, reason: str
) -> None:
    observation, policy = inputs([1, 0])
    observation["payload"]["readings"][0]["rows"][-1][field] = value
    _reject(observation, policy, reason)


@pytest.mark.usefixtures("no_statistics")
@pytest.mark.parametrize(
    "fault,reason",
    [
        ("equal-sequence", "CONFLICTING_CASE_ORDER"),
        ("duplicate-row", "UNSUPPORTED_CASE_IDENTITY"),
        ("duplicate-reading", "AMBIGUOUS_SELECTOR"),
        ("duplicate-availability", "INVALID_AVAILABILITY"),
        ("observed-without-row", "CONTRADICTORY_AVAILABILITY"),
        ("availability-epoch", "UNSUPPORTED_CASE_IDENTITY"),
        ("availability-repeat", "UNSUPPORTED_CASE_IDENTITY"),
        ("availability-state", "INVALID_AVAILABILITY"),
        ("split-conflict", "CONFLICTING_CASE_CONDITIONS"),
    ],
)
def test_selected_identity_order_split_and_availability_failures(fault: str, reason: str) -> None:
    observation, policy = inputs([1, 0])
    payload = observation["payload"]
    reading = payload["readings"][0]
    rows = reading["rows"]
    item = {
        "instrument": "label",
        "metric_id": "event",
        "case_id": rows[-1]["case_id"],
        "repeat": 0,
        "availability": "observed",
    }
    payload["availability"] = [item]
    if fault == "equal-sequence":
        rows[-1]["sequence"] = rows[0]["sequence"]
    elif fault == "duplicate-row":
        rows[-1]["case_id"] = rows[0]["case_id"]
    elif fault == "duplicate-reading":
        payload["readings"].append(copy.deepcopy(reading))
    elif fault == "duplicate-availability":
        payload["availability"].append(copy.deepcopy(item))
    elif fault == "observed-without-row":
        rows.pop()
    elif fault == "availability-epoch":
        item["epoch"] = 0
    elif fault == "availability-repeat":
        item["repeat"] = True
    elif fault == "availability-state":
        item["availability"] = "unknown"
    else:
        item["split"] = "different"
        rows[-1]["split"] = "explore"
    _reject(observation, policy, reason)


@pytest.mark.parametrize("role,field", [(0, "outcome"), (1, "score")])
@pytest.mark.parametrize("state", ["missing", "error"])
def test_selected_role_missingness_does_not_reinterpret_other_fields(
    role: int, field: str, state: str
) -> None:
    observation, policy = inputs([1, 0])
    reading = observation["payload"]["readings"][role]
    row = reading["rows"][-1]
    row[field] = None
    observation["payload"]["availability"] = [
        {
            "instrument": reading["instrument"],
            "metric_id": reading["metric_id"],
            "case_id": row["case_id"],
            "repeat": 0,
            "availability": state,
        }
    ]
    result = _measure(observation, policy)
    population = result["population"]["outcomes" if role == 0 else "predictions"]
    assert row["case_id"] in population["error_ids" if state == "error" else "missing_ids"]
    row[field] = 0
    _reject(observation, policy, "CONTRADICTORY_AVAILABILITY")


@pytest.mark.parametrize("bound", ["readings", "rows", "availability"])
def test_current_bundle_population_boundaries_remain_enforced(bound: str) -> None:
    observation, policy = inputs([1])
    payload = observation["payload"]
    extra: dict[str, Any]
    if bound == "readings":
        payload["readings"].extend(
            {"instrument": "unused", "rows": []}
            for _ in range(read_input.MAX_CELLS - len(payload["readings"]))
        )
        extra = {"instrument": "unused", "rows": []}
    elif bound == "availability":
        payload["availability"] = [
            {"instrument": "unused"} for _ in range(2 * read_input.MAX_CASES)
        ]
        extra = {"instrument": "unused"}
    else:
        payload["readings"].append(
            {"instrument": "unused", "rows": [{} for _ in range(2 * read_input.MAX_CASES - 2)]}
        )
        extra = {}
    assert _measure(observation, policy)["schema"] == "read.v2"
    if bound == "rows":
        payload["readings"][-1]["rows"].append(extra)
    else:
        payload[bound].append(extra)
    with pytest.raises(ReadInputError) as caught:
        _measure(observation, policy)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INPUT_LIMIT_EXCEEDED",
        "WORK_LIMIT_EXCEEDED",
        "/observation",
    )
