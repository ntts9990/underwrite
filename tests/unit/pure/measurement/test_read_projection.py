"""Composition regressions for the explicit fixed measurement profile."""

import copy
import json
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest
from _repo_paths import repo_root

from underwrite.measurement import availability
from underwrite.measurement.read import assemble_read
from underwrite.measurement.read_input import ReadInputError, prepare

FIXTURES = repo_root(Path(__file__).resolve()) / "fixtures/examples/measurement"
DIGEST = "sha256:" + "a" * 64
JsonObject = dict[str, Any]
Inputs = tuple[JsonObject, JsonObject]


@pytest.fixture
def inputs() -> Inputs:
    return cast(
        Inputs,
        tuple(
            json.loads((FIXTURES / name).read_text()) for name in ("read-input.json", "policy.json")
        ),
    )


def run(observation: JsonObject, policy: JsonObject) -> JsonObject:
    return assemble_read(observation, policy, observation_digest=DIGEST, policy_digest=DIGEST)


def test_partial_and_below_quorum(inputs: Inputs) -> None:
    observation, policy = inputs
    full = run(observation, policy)
    policy["required_instruments"].append("inspect")
    partial = run(observation, policy)
    assert full["verdict"] == "pass"
    assert partial["verdict"] == "warn"
    assert partial["score"] == full["score"]
    assert partial["availability"]["state"] == "partial"
    assert partial["reason_codes"] == ["required_instrument_error"]
    policy["quorum"] = 2
    missing = run(observation, policy)
    assert missing["verdict"] == "not_measured"
    assert missing["score"] is None
    assert "below_quorum" in missing["reason_codes"]


@pytest.mark.parametrize("loss", ["error", "missing"])
def test_not_measured_loss_reasons_match_availability_cap(inputs: Inputs, loss: str) -> None:
    observation, policy = inputs
    payload = observation["payload"]
    selected = payload["readings"][1]
    payload["availability"] = [
        {**entry, "epoch": None, "availability": loss}
        for entry in payload["availability"]
        if entry["instrument"] == selected["instrument"]
    ]
    selected["rows"] = []
    result = run(observation, policy)
    capped = availability.cap(
        availability.make_panel_read("pass", 1.0, False),
        availability.assess(
            availability.make_policy(policy["required_instruments"], policy["quorum"]),
            [],
            {selected["instrument"]: loss},
        ),
    )
    assert result["verdict"] == capped.verdict == "not_measured"
    assert result["reason_codes"] == ["signal_not_measured", *capped.reason_codes]
    assert capped.reason_codes[-1] == f"required_instrument_{loss}"


def test_not_measured_two_losses_follow_availability_order(inputs: Inputs) -> None:
    """Standalone behavior and boundary checks."""
    observation, policy = inputs
    payload = observation["payload"]
    payload["readings"][1]["rows"] = []
    payload["availability"] = [
        {**entry, "epoch": None, "availability": "missing", "split": "unspecified"}
        if entry["instrument"] == "deepeval"
        else entry
        for entry in payload["availability"]
    ]
    policy["required_instruments"] = ["deepeval", "inspect"]
    policy["stratification"] = {"axes": ["split"], "mode": "split", "requested": []}
    result = run(observation, policy)
    expected = [
        "signal_not_measured",
        "below_quorum",
        "required_instrument_error",
        "required_instrument_missing",
    ]
    assert result["verdict"] == "not_measured"
    assert result["reason_codes"] == expected
    assert [stratum["reason_codes"] for stratum in result["strata"]] == [expected]


def test_quorum_does_not_hide_missing_method(inputs: Inputs) -> None:
    observation, policy = inputs
    policy["inputs"]["eprocess"]["metric_id"] = "unavailable"
    result = run(observation, policy)
    assert result["availability"]["state"] == "full"
    assert result["verdict"] == "not_measured"
    assert result["jury"]["e_value"] is None
    assert result["signals"][1]["availability"] == "measured"


def test_source_verdict_is_ignored(inputs: Inputs) -> None:
    observation, policy = inputs
    baseline = run(observation, policy)
    observation["payload"]["read"] = {"verdict": "fail", "e_value": 1e99}
    assert run(observation, policy) == baseline


def test_ordered_union_preserves_stream_order_and_appends_missing(inputs: Inputs) -> None:
    observation, policy = inputs
    payload = observation["payload"]
    row = copy.deepcopy(payload["availability"][-1])
    row.update(case_id="absent-last", availability="missing")
    payload["availability"].insert(0, row)
    prepared = prepare(observation, policy)
    assert [case.case_id for case in prepared.cases] == [
        "bfcl/simple_python/case-001",
        "bfcl/simple_python/case-002",
        "bfcl/simple_python/case-003",
        "absent-last",
    ]
    result = run(observation, policy)
    assert result["eprocess"] is None
    assert result["verdict"] == "not_measured"


def test_conflicting_method_order_rejects(inputs: Inputs) -> None:
    observation, policy = inputs
    reading = copy.deepcopy(observation["payload"]["readings"][1])
    reading["metric_id"] = "other"
    reading["rows"].reverse()
    for index, row in enumerate(reading["rows"]):
        row["sequence"] = index
    observation["payload"]["readings"].append(reading)
    policy["inputs"]["calibration"]["metric_id"] = "other"
    with pytest.raises(ReadInputError):
        run(observation, policy)


def test_source_capture_matrix_and_unchanged_inputs(inputs: Inputs) -> None:
    observation, _ = inputs
    source = json.loads((FIXTURES / "expected-matrix.json").read_text())
    verdicts: set[str] = set()
    for index, expected in enumerate(source["runs"]):
        name = f"policy-source-{index + 1}.json"
        policy = json.loads((FIXTURES / name).read_text())
        before = copy.deepcopy((observation, policy))
        result = run(observation, policy)
        assert (observation, policy) == before
        assert result["verdict"] == expected["read"]["verdict"]
        assert result["score"] == expected["read"]["score"]
        assert result["escalate"] == expected["read"]["escalate"]
        assert result["eprocess"]["e_value"] == pytest.approx(expected["raw_e_value"], rel=1e-9)
        assert result["jury"]["members"] == 1
        verdicts.add(result["verdict"])
    assert verdicts == {"pass", "warn", "fail"}


def test_missing_calibration_preserves_engine_and_full_instrument(inputs: Inputs) -> None:
    observation, policy = inputs
    policy["inputs"]["calibration"]["metric_id"] = "missing"
    result = run(observation, policy)
    assert result["jury"]["e_value"] == result["eprocess"]["e_value"]
    assert result["availability"]["state"] == "full"
    assert result["verdict"] == "not_measured"
    assert result["score"] is None and result["escalate"] is True
    assert "signal_not_measured" in result["reason_codes"]


def test_empty_population_and_no_strata_call(
    inputs: Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation, policy = inputs
    observation["payload"]["availability"] = []
    observation["payload"]["readings"] = []

    def fail_stratify(*a: object, **k: object) -> NoReturn:
        pytest.fail("none mode")

    monkeypatch.setattr("underwrite.measurement.stratify.stratify", fail_stratify)
    result = run(observation, policy)
    assert result["eprocess"] is None
    assert result["sources"]["observations"] == [DIGEST]
    assert result["verdict"] == "not_measured" and result["escalate"] is True
    assert {"no_observations", "below_quorum"} <= set(result["reason_codes"])
    assert result["strata"] == []


def test_split_cells_reuse_read_and_noninferential_loss(inputs: Inputs) -> None:
    observation, policy = inputs
    policy["required_instruments"].append("inspect")
    policy["stratification"] = {
        "mode": "split",
        "axes": ["split"],
        "requested": [{"split": "empty"}],
    }
    result = run(observation, policy)
    empty, cell = result["strata"]
    assert empty["reason_codes"] == ["no_observations"]
    assert cell["verdict"] == result["verdict"] == "warn"
    assert cell["score"] == result["score"]
    policy["min_cell_n"] = 4
    sparse = run(observation, policy)
    assert sparse["verdict"] == result["verdict"]
    assert sparse["strata"][1]["reason_codes"] == ["cell_below_min_n"]


def test_calibration_floors_do_not_erase_brier(inputs: Inputs) -> None:
    observation, policy = inputs
    policy["min_bin_n"] = 4
    policy["calibration"]["k"] = 4
    result = run(observation, policy)
    assert result["calibration"]["brier"]["availability"] == "measured"
    assert result["calibration"]["ece"]["reason_codes"] == ["bin_below_min_n"]
    assert result["calibration"]["pass_k"]["reason_codes"] == ["n_below_k"]
    assert result["verdict"] == "not_measured"


def test_budget_before_first_trial_is_typed_failure(inputs: Inputs) -> None:
    observation, policy = inputs
    for row in observation["payload"]["readings"][1]["rows"]:
        row["payload"]["cost"] = 2
    policy["eprocess"]["max_cost"] = 1
    with pytest.raises(ReadInputError) as error:
        run(observation, policy)
    assert (error.value.code, error.value.reason, error.value.location) == (
        "INVALID_POLICY",
        "NO_TRIALS",
        "/policy",
    )


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 2])
def test_bad_consumed_outcomes_reject(inputs: Inputs, value: float | bool) -> None:
    observation, policy = inputs
    observation["payload"]["readings"][1]["rows"][0]["outcome"] = value
    with pytest.raises(ReadInputError):
        run(observation, policy)


def test_missing_slot_does_not_shrink_population(inputs: Inputs) -> None:
    observation, policy = inputs
    observation["payload"]["readings"][1]["rows"][0]["outcome"] = None
    result = run(observation, policy)
    assert result["eprocess"] is None
    assert result["calibration"]["brier"]["n"] == len(observation["payload"]["readings"][1]["rows"])
    assert result["calibration"]["brier"]["value"] is None


def test_result_has_no_mutable_input_alias(inputs: Inputs) -> None:
    observation, policy = inputs
    result = run(observation, policy)
    result["subject"]["conditions"]["split"] = "changed"
    result["availability"]["required_instruments"].append("changed")
    assert policy["subject"]["conditions"] == {}
    assert "changed" not in policy["required_instruments"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("epoch", 0),
        ("repeat", True),
        ("repeat", 1),
        ("case_id", "bad\x00identity"),
        ("case_id", "e\u0301"),
        ("sequence", True),
        ("sequence", -1),
        ("score", float("nan")),
    ],
)
def test_unsupported_row_identities_and_values(inputs: Inputs, field: str, value: object) -> None:
    observation, policy = inputs
    observation["payload"]["readings"][1]["rows"][0][field] = value
    with pytest.raises(ReadInputError):
        run(observation, policy)


def test_required_observed_metadata_cannot_vote(inputs: Inputs) -> None:
    observation, policy = inputs
    policy["required_instruments"].append("inspect")
    observation["payload"]["availability"][0]["availability"] = "observed"
    with pytest.raises(ReadInputError, match="UNSUPPORTED_REQUIRED_EVIDENCE"):
        run(observation, policy)


def test_observed_error_contradiction_rejects(inputs: Inputs) -> None:
    observation, policy = inputs
    observation["payload"]["availability"][-1]["availability"] = "error"
    with pytest.raises(ReadInputError, match="CONTRADICTORY_AVAILABILITY"):
        run(observation, policy)


def test_missing_only_split_requires_and_accepts_explicit_metadata(inputs: Inputs) -> None:
    observation, policy = inputs
    policy["stratification"] = {"mode": "split", "axes": ["split"], "requested": []}
    item = copy.deepcopy(observation["payload"]["availability"][-1])
    item.update(case_id="missing-only", availability="missing")
    observation["payload"]["availability"].append(item)
    with pytest.raises(ReadInputError, match="MISSING_SPLIT"):
        run(observation, policy)
    item["split"] = "lost"
    result = run(observation, policy)
    assert result["strata"][0]["key"] == {"split": "lost"}
    assert result["strata"][0]["availability"] == "not_measured"


@pytest.mark.parametrize(
    "path,value",
    [
        (("calibration", "bins"), 0),
        (("calibration", "bins"), 129),
        (("calibration", "k"), 2001),
        (("calibration", "pass_ece"), True),
        (("panel", "warn_at"), 1),
        (("eprocess", "p0"), 0.4),
        (("fusion", "combine"), "product"),
        (("quorum",), 2),
        (("required_instruments",), []),
        (("required_instruments",), ["deepeval", "deepeval"]),
        (("seed",), None),
        (("subject", "conditions"), {"split": "different"}),
    ],
)
def test_invalid_policy_stops_before_engine(
    inputs: Inputs, monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...], value: object
) -> None:
    observation, policy = inputs
    target = policy
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    def fail_run_stream(*a: object) -> NoReturn:
        pytest.fail("invalid policy reached engine")

    monkeypatch.setattr("underwrite.measurement.eprocess.run_stream", fail_run_stream)
    with pytest.raises(ReadInputError):
        run(observation, policy)


def expanded(observation: JsonObject, count: int, cells: int) -> JsonObject:
    rows: list[JsonObject] = []
    for index in range(count):
        rows.append(
            {
                "case_id": f"case-{index}",
                "repeat": 0,
                "sequence": index,
                "outcome": index % 2,
                "score": 0.5,
                "split": f"split-{index * cells // count}",
                "payload": {},
            }
        )
    observation["payload"]["readings"] = [
        {"instrument": "deepeval", "metric_id": "AnswerRelevancy", "rows": rows}
    ]
    observation["payload"]["availability"] = []
    return observation


@pytest.mark.parametrize("count,cells", [(2001, 1), (129, 129)])
def test_union_and_discovered_cell_limits_precede_statistics(
    inputs: Inputs, monkeypatch: pytest.MonkeyPatch, count: int, cells: int
) -> None:
    observation, policy = inputs
    expanded(observation, count, cells)
    policy["stratification"] = {"mode": "split", "axes": ["split"], "requested": []}

    def fail_run_stream(*a: object) -> NoReturn:
        pytest.fail("oversized input reached engine")

    monkeypatch.setattr("underwrite.measurement.eprocess.run_stream", fail_run_stream)
    with pytest.raises(ReadInputError) as error:
        run(observation, policy)
    assert error.value.code == "INPUT_LIMIT_EXCEEDED"


def test_ordered_union_can_insert_a_case_before_shared_anchor(inputs: Inputs) -> None:
    observation, policy = inputs
    reading = copy.deepcopy(observation["payload"]["readings"][1])
    reading["metric_id"] = "other"
    row = copy.deepcopy(reading["rows"][0])
    row["case_id"] = "prior"
    reading["rows"].insert(0, row)
    for index, row in enumerate(reading["rows"]):
        row["sequence"] = index
    observation["payload"]["readings"].append(reading)
    policy["inputs"]["calibration"]["metric_id"] = "other"
    prepared = prepare(observation, policy)
    assert prepared.cases[0].case_id == "prior"
    result = run(observation, policy)
    assert result["eprocess"] is None
    assert result["calibration"]["brier"]["availability"] == "measured"


def test_produced_absence_shapes_validate_without_losing_causes(inputs: Inputs) -> None:
    jsonschema = pytest.importorskip("jsonschema", reason="optional shell contract validator")
    contracts = FIXTURES.parents[2] / "contracts"
    schema = json.loads((contracts / "read.v1.schema.json").read_text())
    seed = json.loads((contracts / "statistical_seed.v1.schema.json").read_text())
    schema["properties"]["seed"] = {k: v for k, v in seed.items() if not k.startswith("$")}
    observation, policy = inputs
    policy["min_bin_n"] = len(observation["payload"]["readings"][1]["rows"]) + 1
    result = run(observation, policy)
    assert result["availability"]["state"] == "full"
    assert result["reason_codes"] == ["bin_below_min_n", "signal_not_measured"]
    jsonschema.validate(result, schema)
    policy["required_instruments"].append("inspect")
    policy["quorum"] = 2
    result = run(observation, policy)
    assert result["escalate"] is True and "below_quorum" in result["reason_codes"]
    jsonschema.validate(result, schema)
