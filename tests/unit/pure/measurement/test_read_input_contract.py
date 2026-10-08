"""Fixed-profile input contracts: independent diagnostics and bounded populations."""

import copy
import json
from typing import NoReturn, cast

import pytest
from test_read_projection import FIXTURES, Inputs, JsonObject, expanded, run
from test_read_projection import inputs as inputs

from underwrite.measurement.read_input import ReadInputError, Slot, prepare


@pytest.fixture(autouse=True)
def no_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("input validation reached statistics")

    for target in ("eprocess.run_stream", "calibration.brier", "jury.fuse"):
        monkeypatch.setattr(f"underwrite.measurement.{target}", fail)


def rejected(inputs: Inputs, reason: str, location: str, code: str | None = None) -> None:
    with pytest.raises(ReadInputError) as caught:
        run(*inputs)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        code or ("INVALID_POLICY" if location == "/policy" else "INVALID_OBSERVATION"),
        reason,
        location,
    )
    assert str(caught.value) == reason


@pytest.mark.parametrize("field", ["schema", "profile"])
def test_policy_profile_guards_are_independent(inputs: Inputs, field: str) -> None:
    prepare(*inputs)
    inputs[1][field] = "unsupported"
    rejected(inputs, "UNSUPPORTED_POLICY_PROFILE", "/policy", "UNSUPPORTED_PROFILE")


@pytest.mark.parametrize("field", ["source", "kind"])
def test_observation_profile_guards_are_independent(inputs: Inputs, field: str) -> None:
    prepare(*inputs)
    inputs[0][field] = "unsupported"
    rejected(inputs, "UNSUPPORTED_OBSERVATION_PROFILE", "/observation", "UNSUPPORTED_PROFILE")


@pytest.mark.parametrize(
    "path,value,reason",
    [
        (("eprocess", "p0"), 0.4, "NONCANONICAL_EPROCESS"),
        (("eprocess", "prior_a"), 2, "NONCANONICAL_EPROCESS"),
        (("eprocess", "prior_b"), 2, "NONCANONICAL_EPROCESS"),
        (("eprocess", "pass_e"), 21, "NONCANONICAL_EPROCESS"),
        (("calibration", "prediction_event"), "other", "INVALID_CALIBRATION_MAPPING"),
        (("calibration", "prediction_field"), "outcome", "INVALID_CALIBRATION_MAPPING"),
        (("calibration", "label_field"), "score", "INVALID_CALIBRATION_MAPPING"),
        (("calibration", "warn_ece"), 0.5, "INVALID_CALIBRATION_MAPPING"),
        (("panel", "warn_at"), 0.8, "INVALID_PANEL_BANDS"),
        (("fusion", "independence"), True, "INVALID_FUSION"),
        (("stratification", "mode"), "other", "INVALID_STRATIFICATION"),
        (("stratification", "axes"), ["split"], "INVALID_STRATIFICATION"),
        (("stratification", "requested"), [{"split": "x"}], "INVALID_STRATIFICATION"),
        (("stratification", "requested"), {}, "INVALID_STRATIFICATION"),
        (("subject", "extra"), "x", "INVALID_SUBJECT"),
        (("subject", "conditions"), {"other": "x"}, "INVALID_SUBJECT"),
        (("subject", "label"), 1, "INVALID_SUBJECT"),
        (("subject", "subject_id"), "bad space", "INVALID_SUBJECT"),
        (("subject", "conditions"), {"split": "other"}, "SUBJECT_CONDITION_CONFLICT"),
        (("required_instruments",), "deepeval", "INVALID_REQUIRED_INSTRUMENTS"),
        (("required_instruments",), [], "INVALID_POLICY_FIELDS"),
        (("required_instruments",), ["deepeval", "deepeval"], "INVALID_POLICY_FIELDS"),
        (("calibration",), None, "INVALID_POLICY_FIELDS"),
        (("inputs",), {}, "INVALID_POLICY_FIELDS"),
    ],
)
def test_policy_single_fault_diagnostics(
    inputs: Inputs, path: tuple[str, ...], value: object, reason: str
) -> None:
    prepare(*inputs)
    target = inputs[1]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    rejected(
        inputs, reason, "/observation" if reason == "SUBJECT_CONDITION_CONFLICT" else "/policy"
    )


def _policy_key_paths() -> list[tuple[str, ...]]:
    def walk(node: object, prefix: tuple[str, ...]) -> list[tuple[str, ...]]:
        if not isinstance(node, dict):
            return []
        mapping = cast(JsonObject, node)
        return [
            path
            for key, value in mapping.items()
            for path in [(*prefix, key), *walk(value, (*prefix, key))]
        ]

    return walk(json.loads((FIXTURES / "policy.json").read_text()), ())


@pytest.mark.parametrize("path", _policy_key_paths(), ids="/".join)
def test_every_policy_key_is_required(inputs: Inputs, path: tuple[str, ...]) -> None:
    """Standalone behavior and boundary checks."""
    target = inputs[1]
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    with pytest.raises(ReadInputError) as caught:
        run(*inputs)
    assert caught.value.location == "/policy"


@pytest.mark.parametrize("field", ["min_cell_n", "min_bin_n", "seed"])
@pytest.mark.parametrize("value", [True, 1.5, -1, 2**53, None])
def test_integer_policy_diagnostics(inputs: Inputs, field: str, value: object) -> None:
    inputs[1][field] = value
    rejected(inputs, "INVALID_INTEGER", "/policy")


@pytest.mark.parametrize("field", ["min_cell_n", "min_bin_n"])
def test_integer_upper_limit_is_accepted(inputs: Inputs, field: str) -> None:
    inputs[1][field] = 2**53 - 1
    assert prepare(*inputs).policy[field] == 2**53 - 1


@pytest.mark.parametrize("seed", [0, 43, 2**53 - 1])
def test_noncanonical_seed_is_refused_before_labelling_read_v1(inputs: Inputs, seed: int) -> None:
    """read.v1 admits only statistical_seed.v1's const, so the pure loader does too."""
    inputs[1]["seed"] = seed
    rejected(inputs, "INVALID_INTEGER", "/policy")


@pytest.mark.parametrize("field", ["score", "cost"])
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -float("inf"), -0.1])
def test_nonfinite_and_boolean_numbers_reject(inputs: Inputs, field: str, value: object) -> None:
    row = inputs[0]["payload"]["readings"][1]["rows"][0]
    (row["payload"] if field == "cost" else row)[field] = value
    rejected(inputs, "INVALID_NUMBER", "/observation")


@pytest.mark.parametrize("field", ["pass_at", "warn_at", "escalate_at", "conflict_confidence"])
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -0.1, 1.1])
def test_panel_numeric_fields_reject_independently(
    inputs: Inputs, field: str, value: object
) -> None:
    inputs[1]["panel"][field] = value
    rejected(inputs, "INVALID_NUMBER", "/policy")


@pytest.mark.parametrize("value", [None, "", 3, "e\u0301", "bad\x00id", "x" * 129])
@pytest.mark.parametrize("location", ["/policy", "/observation"])
def test_identity_diagnostics(inputs: Inputs, value: object, location: str) -> None:
    if location == "/policy":
        inputs[1]["subject"]["subject_id"] = value
    else:
        inputs[0]["payload"]["readings"][1]["rows"][0]["case_id"] = value
    rejected(inputs, "INVALID_IDENTITY", location)


def test_valid_identity_subject_and_numeric_endpoints(inputs: Inputs) -> None:
    observation, policy = inputs
    policy["subject"] = {
        "subject_id": "UPPER.case:01-REF",
        "conditions": {"split": "é"},
        "label": "",
    }
    policy["eprocess"]["null_id"] = "x" * 128
    rows = observation["payload"]["readings"][1]["rows"]
    observation["payload"]["availability"] = []
    for row in rows:
        row["split"] = "é"
        row["score"] = 0
        row["payload"]["cost"] = 0
    rows[0]["case_id"] = "x" * 128
    rows[-1]["score"] = 1
    rows[-1]["sequence"] = 2**53 - 1
    prepared = prepare(*inputs)
    assert prepared.cases[0].case_id == "x" * 128
    assert prepared.cases[0].conditions == (("split", "é"),)


@pytest.mark.parametrize("slot", ["eprocess", "calibration"])
@pytest.mark.parametrize("fault", ["extra", "missing", "not-required"])
def test_selector_contract(inputs: Inputs, slot: str, fault: str) -> None:
    selector = inputs[1]["inputs"][slot]
    if fault == "extra":
        selector["extra"] = "ignored"
    elif fault == "missing":
        del selector["metric_id"]
    else:
        selector["instrument"] = "unlisted"
    rejected(
        inputs,
        "SELECTOR_NOT_REQUIRED" if fault == "not-required" else "INVALID_SELECTOR",
        "/policy",
    )


@pytest.mark.parametrize("field", ["readings", "availability", "schema_version"])
def test_missing_bundle_fields(inputs: Inputs, field: str) -> None:
    del inputs[0]["payload"][field]
    rejected(inputs, "INVALID_BUNDLE_FIELDS", "/observation")


@pytest.mark.parametrize("field", ["readings", "availability"])
@pytest.mark.parametrize("value", [None, {}, [None]])
def test_malformed_bundle_containers(inputs: Inputs, field: str, value: object) -> None:
    inputs[0]["payload"][field] = value
    rejected(inputs, "INVALID_BUNDLE_FIELDS", "/observation")


@pytest.mark.parametrize("field", ["case_id", "sequence", "repeat", "outcome", "score"])
def test_missing_consumed_row_fields(inputs: Inputs, field: str) -> None:
    del inputs[0]["payload"]["readings"][1]["rows"][0][field]
    rejected(inputs, "INVALID_BUNDLE_FIELDS", "/observation")


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
def test_row_metadata_diagnostics(inputs: Inputs, field: str, value: object, reason: str) -> None:
    inputs[0]["payload"]["readings"][1]["rows"][-1][field] = value
    rejected(inputs, reason, "/observation")


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
def test_selected_metadata_failures(inputs: Inputs, fault: str, reason: str) -> None:
    payload = inputs[0]["payload"]
    rows = payload["readings"][1]["rows"]
    item = payload["availability"][-1]
    if fault == "equal-sequence":
        rows[-1]["sequence"] = rows[-2]["sequence"]
    elif fault == "duplicate-row":
        rows[-1]["case_id"] = rows[0]["case_id"]
    elif fault == "duplicate-reading":
        payload["readings"].append(copy.deepcopy(payload["readings"][1]))
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
    rejected(inputs, reason, "/observation")


@pytest.mark.parametrize("state", ["error", "missing"])
@pytest.mark.parametrize("field", ["outcome", "score"])
def test_unavailable_rows_require_both_values_null(inputs: Inputs, state: str, field: str) -> None:
    payload = inputs[0]["payload"]
    payload["availability"][-1]["availability"] = state
    row = payload["readings"][1]["rows"][-1]
    row["outcome"] = row["score"] = None
    prepared = prepare(*inputs)
    assert prepared.cases[-1].payload == (Slot(None, None, None, state),) * 2
    row[field] = 0
    rejected(inputs, "CONTRADICTORY_AVAILABILITY", "/observation")


@pytest.mark.parametrize(
    "bound", ["cases", "rows", "availability", "readings", "required", "requested", "cells"]
)
def test_each_work_limit_accepts_boundary_then_rejects_adjacent(inputs: Inputs, bound: str) -> None:
    observation, policy = inputs
    expanded(observation, 2000 if bound == "cases" else 1, 1)
    payload = observation["payload"]
    if bound == "cases":
        prepared = prepare(*inputs)
        assert [case.case_id for case in prepared.cases] == [f"case-{i}" for i in range(2000)]
        expanded(observation, 2001, 1)
    elif bound == "rows":
        padding: JsonObject = {
            "instrument": "unused",
            "metric_id": "padding",
            "rows": [{} for _ in range(3999)],
        }
        payload["readings"].append(padding)
        assert len(prepare(*inputs).cases) == 1
        padding["rows"].append({})
    elif bound == "availability":
        payload["availability"] = [{"instrument": "unused"} for _ in range(4000)]
        assert len(prepare(*inputs).cases) == 1
        payload["availability"].append({"instrument": "unused"})
    elif bound == "readings":
        payload["readings"].extend({"instrument": "unused", "rows": []} for _ in range(127))
        assert len(prepare(*inputs).cases) == 1
        payload["readings"].append({"instrument": "unused", "rows": []})
    elif bound == "required":
        policy["required_instruments"].extend(f"unused-{i}" for i in range(127))
        assert prepare(*inputs).extra_losses == {f"unused-{i}": "missing" for i in range(127)}
        policy["required_instruments"].append("one-more")
    elif bound == "requested":
        payload["readings"] = []
        requested = [{"split": str(i)} for i in range(128)]
        policy["stratification"] = {
            "mode": "split",
            "axes": ["split"],
            "requested": requested,
        }
        assert prepare(*inputs).cases == ()
        requested.append({"split": "one-more"})
    else:
        requested = [{"split": str(i)} for i in range(127)]
        policy["stratification"] = {
            "mode": "split",
            "axes": ["split"],
            "requested": requested,
        }
        assert len(prepare(*inputs).cases) == 1
        requested.append({"split": "one-more"})
    rejected(
        inputs,
        "WORK_LIMIT_EXCEEDED",
        "/policy" if bound in ("required", "requested") else "/observation",
        "INPUT_LIMIT_EXCEEDED",
    )


def test_distinct_streams_preserve_two_incoming_edges_and_missing_slots(inputs: Inputs) -> None:
    observation, policy = inputs
    expanded(observation, 3, 1)
    payload = observation["payload"]
    first = payload["readings"][0]
    second = copy.deepcopy(first)
    second["instrument"] = "second"
    first["rows"] = [first["rows"][0], first["rows"][2]]
    second["rows"] = [second["rows"][1], second["rows"][2]]
    payload["readings"].append(second)
    policy["required_instruments"].append("second")
    policy["inputs"]["calibration"]["instrument"] = "second"
    prepared = prepare(*inputs)
    assert prepared.instruments == ("deepeval", "second")
    assert [case.case_id for case in prepared.cases] == ["case-0", "case-1", "case-2"]
    assert prepared.cases[0].payload == (Slot(0, 0.5, None, None), None)
    assert prepared.cases[1].payload == (None, Slot(1, 0.5, None, None))
    assert prepared.cases[2].payload == (Slot(0, 0.5, None, None),) * 2


def test_availability_only_cases_and_loss_precedence(inputs: Inputs) -> None:
    observation, policy = inputs
    expanded(observation, 1, 1)
    payload = observation["payload"]
    payload["availability"] = [
        {
            "instrument": "deepeval",
            "metric_id": "AnswerRelevancy",
            "case_id": "absent",
            "repeat": 0,
            "availability": "missing",
            "split": "lost",
        }
    ]
    policy["required_instruments"].append("extra")
    payload["availability"].extend(
        {"instrument": "extra", "availability": state} for state in ("missing", "error")
    )
    prepared = prepare(*inputs)
    assert [case.case_id for case in prepared.cases] == ["case-0", "absent"]
    assert prepared.cases[-1].conditions == (("split", "lost"),)
    assert prepared.cases[-1].payload == (Slot(None, None, None, "missing"),) * 2
    assert prepared.extra_losses == {"extra": "error"}


@pytest.mark.parametrize(
    "field,limit", [("bins", 128), ("k", 2000), ("anchor_min_samples", 2**53 - 1)]
)
def test_calibration_integer_boundaries(inputs: Inputs, field: str, limit: int) -> None:
    inputs[1]["calibration"][field] = limit
    assert prepare(*inputs).policy["calibration"][field] == limit
    inputs[1]["calibration"][field] = limit + 1
    rejected(inputs, "INVALID_INTEGER", "/policy")
    inputs[1]["calibration"][field] = 0
    rejected(inputs, "INVALID_INTEGER", "/policy")


@pytest.mark.parametrize("field", ["pass_ece", "warn_ece"])
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -0.1, 1.1])
def test_calibration_number_diagnostics(inputs: Inputs, field: str, value: object) -> None:
    inputs[1]["calibration"][field] = value
    rejected(inputs, "INVALID_NUMBER", "/policy")


@pytest.mark.parametrize("cell", [{}, {"split": "x", "extra": "y"}, {"other": "x"}])
def test_requested_cells_require_exact_axis(inputs: Inputs, cell: JsonObject) -> None:
    inputs[1]["stratification"] = {"mode": "split", "axes": ["split"], "requested": [cell]}
    rejected(inputs, "INVALID_REQUESTED_CELL", "/policy")


def test_split_metadata_matches_across_streams_then_rejects_conflict(inputs: Inputs) -> None:
    observation, policy = inputs
    payload = observation["payload"]
    second = copy.deepcopy(payload["readings"][1])
    second["metric_id"] = "other"
    payload["readings"].append(second)
    policy["inputs"]["calibration"]["metric_id"] = "other"
    payload["availability"][-1]["split"] = "unspecified"
    assert all(case.conditions == (("split", "unspecified"),) for case in prepare(*inputs).cases)
    second["rows"][-1]["split"] = "other"
    rejected(inputs, "CONFLICTING_CASE_CONDITIONS", "/observation")


def test_matching_requested_discovered_cells_are_deduplicated(inputs: Inputs) -> None:
    expanded(inputs[0], 128, 128)
    inputs[1]["stratification"] = {
        "mode": "split",
        "axes": ["split"],
        "requested": [{"split": f"split-{i}"} for i in range(128)],
    }
    assert {case.conditions for case in prepare(*inputs).cases} == {
        (("split", f"split-{i}"),) for i in range(128)
    }


def test_policy_and_bundle_shape_failures_are_safe(inputs: Inputs) -> None:
    observation, policy = inputs
    del policy["min_cell_n"]
    rejected(inputs, "INVALID_POLICY_FIELDS", "/policy")
    policy["min_cell_n"] = 1
    observation["payload"]["schema_version"] = "unsupported"
    rejected(inputs, "INVALID_BUNDLE_VERSION", "/observation")
    observation["payload"]["schema_version"] = "underwrite.evidence-bundle.v1"
    observation["payload"]["readings"][1]["rows"] = {}
    rejected(inputs, "INVALID_BUNDLE_FIELDS", "/observation")


@pytest.mark.parametrize("value", [True, 0.5, float("nan"), float("inf"), -1, 2])
def test_outcome_integer_diagnostic(inputs: Inputs, value: object) -> None:
    inputs[0]["payload"]["readings"][1]["rows"][0]["outcome"] = value
    rejected(inputs, "INVALID_INTEGER", "/observation")


def test_split_mode_requires_case_metadata(inputs: Inputs) -> None:
    inputs[1]["stratification"] = {"mode": "split", "axes": ["split"], "requested": []}
    row = inputs[0]["payload"]["readings"][1]["rows"][0]
    del row["split"]
    rejected(inputs, "MISSING_SPLIT", "/observation")
    row["split"] = "restored"
    assert prepare(*inputs).cases[0].conditions == (("split", "restored"),)
