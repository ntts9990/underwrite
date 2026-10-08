"""Standalone behavior and boundary checks."""

import copy
import math
import sys

import pytest
from test_read_projection import DIGEST, Inputs, JsonObject, run
from test_read_projection import inputs as inputs

from underwrite.measurement.read import PANEL_STAGE_DIGITS, assemble_read
from underwrite.measurement.read_input import ReadInputError


def test_consumed_cost_overflow_is_observation_error(inputs: Inputs) -> None:
    observation, policy = streams(inputs, count=2)
    policy["eprocess"]["max_cost"] = None
    for row in observation["payload"]["readings"][0]["rows"]:
        row["payload"]["cost"] = sys.float_info.max
    before = copy.deepcopy((observation, policy))
    with pytest.raises(ReadInputError) as caught:
        run(observation, policy)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_OBSERVATION",
        "COST_OVERFLOW",
        "/observation",
    )
    assert (observation, policy) == before


def streams(inputs: Inputs, *, prediction: float = 1, count: int = 8) -> Inputs:
    """Replace copied source rows with explicitly synthetic, independently selected streams."""
    observation, policy = copy.deepcopy(inputs)
    readings: list[JsonObject] = []
    for method, instrument in (("eprocess", "binary"), ("calibration", "predictions")):
        policy["inputs"][method] = {"instrument": instrument, "metric_id": method}
        rows: list[JsonObject] = [
            {
                "case_id": f"conformance-{index}",
                "repeat": 0,
                "sequence": index,
                "outcome": 1,
                "score": prediction,
                "split": "conformance",
                "payload": {},
            }
            for index in range(count)
        ]
        readings.append({"instrument": instrument, "metric_id": method, "rows": rows})
    observation["payload"]["readings"] = readings
    observation["payload"]["availability"] = []
    policy["required_instruments"] = ["binary", "predictions"]
    policy["calibration"].update(pass_ece=0.1, warn_ece=0.3)
    return observation, policy


@pytest.mark.parametrize(
    "prediction,anchor,verdict", [(1, 8, "pass"), (0, 8, "fail"), (0, 16, "warn")]
)
def test_strong_evidence_weighting(
    inputs: Inputs, prediction: float, anchor: int, verdict: str
) -> None:
    observation, policy = streams(inputs, prediction=prediction)
    policy["calibration"]["anchor_min_samples"] = anchor
    result = run(observation, policy)
    # Uniform beta prior integrates p**8 to 1/9; the null likelihood is (1/2)**8.
    expected_e = (1 / 9) / (0.5**8)
    stage_confidence = round(1 - 1 / expected_e, PANEL_STAGE_DIGITS)
    confidence = min(1, 8 / anchor)
    weight = {"pass": 1, "warn": 0.5, "fail": 0}[verdict]
    score = round((stage_confidence + weight * confidence) / (stage_confidence + confidence), 4)
    assert result["eprocess"]["e_value"] == pytest.approx(expected_e)
    assert result["jury"]["e_value"] == pytest.approx(expected_e)
    assert result["jury"]["members"] == 1
    assert result["signals"][1] == {
        "method": "calibration",
        "instrument": "predictions",
        "availability": "measured",
        "verdict": verdict,
        "score": prediction,
        "confidence": confidence,
        "e_value": None,
        "null_id": None,
        "reason_codes": [],
    }
    assert result["signals"][0]["instrument"] == "binary"
    assert result["score"] == score
    assert result["verdict"] == ("pass" if score >= policy["panel"]["pass_at"] else "warn")
    assert result["escalate"] is (prediction == 0)
    assert result["availability"]["available"] == ["binary", "predictions"]


def test_panel_stage_digits_round_a_different_quantity_from_the_eprocess_score(
    inputs: Inputs,
) -> None:
    """Standalone behavior and boundary checks."""
    observation, policy = streams(inputs, prediction=0)
    result = run(observation, policy)
    eprocess_score, stage = 1 - 9 / 256, round(1 - 9 / 256, PANEL_STAGE_DIGITS)
    assert result["jury"]["e_value"] == pytest.approx(256 / 9)
    assert result["signals"][0]["score"] == round(eprocess_score, 6) != stage
    # A pass stage (e >= pass_e) weighs 1 and the fail calibration signal 0, at confidence 1.
    assert result["score"] == round(stage / (stage + 1), 4)
    assert result["score"] != round(round(eprocess_score, 6) / (round(eprocess_score, 6) + 1), 4)


@pytest.mark.parametrize("band,expected", [("pass_at", "pass"), ("warn_at", "warn")])
def test_panel_threshold_equality(inputs: Inputs, band: str, expected: str) -> None:
    observation, policy = streams(inputs, prediction=0)
    confidence = round(1 - 9 / 256, PANEL_STAGE_DIGITS)
    boundary = confidence / (confidence + 1)
    policy["panel"][band] = boundary
    assert run(observation, policy)["verdict"] == expected
    policy["panel"][band] = math.nextafter(boundary, 1)
    assert run(observation, policy)["verdict"] == ("warn" if band == "pass_at" else "fail")


@pytest.mark.parametrize("prediction,expected", [(0.75, "pass"), (0.5, "warn"), (0.25, "fail")])
def test_calibration_band_equality(inputs: Inputs, prediction: float, expected: str) -> None:
    observation, policy = streams(inputs, prediction=prediction)
    policy["calibration"].update(pass_ece=0.25, warn_ece=0.5, anchor_min_samples=8)
    result = run(observation, policy)
    assert result["signals"][1]["verdict"] == expected
    assert result["calibration"]["ece"]["value"] == 1 - prediction


def test_signal_precision_is_independent_of_panel_rounding(inputs: Inputs) -> None:
    observation, policy = streams(inputs, prediction=0.123456789)
    result = run(observation, policy)
    expected_signal_score, expected_panel_score = 0.123457, 0.491
    assert result["signals"][1]["score"] == expected_signal_score
    assert result["calibration"]["ece"]["value"] == pytest.approx(1 - 0.123456789)
    assert result["calibration"]["brier"]["value"] == pytest.approx((1 - 0.123456789) ** 2)
    assert result["score"] == expected_panel_score


def test_conflict_confidence_equality_alone_escalates(inputs: Inputs) -> None:
    observation, policy = streams(inputs, prediction=0)
    confidence = round(1 - 9 / 256, PANEL_STAGE_DIGITS)
    policy["panel"].update(escalate_at=1, conflict_confidence=confidence)
    assert run(observation, policy)["escalate"] is True
    policy["panel"]["conflict_confidence"] = math.nextafter(confidence, 1)
    assert run(observation, policy)["escalate"] is False


def test_entropy_alone_escalates(inputs: Inputs) -> None:
    observation, policy = streams(inputs, prediction=0)
    # Strong pass versus low-sample warn has entropy, but no pass/fail conflict.
    policy["calibration"]["anchor_min_samples"] = 16
    policy["panel"].update(escalate_at=0.5, conflict_confidence=1)
    assert run(observation, policy)["escalate"] is True
    policy["panel"]["escalate_at"] = 1
    assert run(observation, policy)["escalate"] is False


def test_weak_stage_alone_escalates(inputs: Inputs) -> None:
    observation, policy = streams(inputs, prediction=0, count=3)
    policy["calibration"]["anchor_min_samples"] = 4
    policy["panel"].update(escalate_at=1, conflict_confidence=1)
    result = run(observation, policy)
    assert result["signals"][0]["verdict"] == result["signals"][1]["verdict"] == "warn"
    assert result["jury"]["disagreement"] == 0
    assert result["escalate"] is True


def test_disagreement_threshold_includes_equality(inputs: Inputs) -> None:
    observation, policy = streams(inputs)
    policy["panel"]["escalate_at"] = 0
    result = run(observation, policy)
    assert result["jury"]["disagreement"] == 0
    assert result["escalate"] is True
    policy["panel"]["escalate_at"] = math.nextafter(0, 1)
    assert run(observation, policy)["escalate"] is False


@pytest.mark.parametrize(
    "method,field", [(0, "score"), (1, "score"), (0, "outcome"), (1, "outcome")]
)
def test_independent_binary_and_prediction_missingness(
    inputs: Inputs, method: int, field: str
) -> None:
    observation, policy = streams(inputs)
    observation["payload"]["readings"][method]["rows"][0][field] = None
    result = run(observation, policy)
    binary_missing = method == 0 and field == "outcome"
    prediction_missing = method == 1
    assert (result["eprocess"] is None) is binary_missing
    assert result["calibration"]["pass_k"] == {
        "availability": "not_measured" if binary_missing else "measured",
        "value": None if binary_missing else 1,
        "n": 8,
        "k": 1,
        "reason_codes": ["signal_not_measured"] if binary_missing else [],
    }
    for name in ("brier", "ece"):
        expected: JsonObject = {
            "availability": "not_measured" if prediction_missing else "measured",
            "value": None if prediction_missing else 0,
            "n": 8,
            "reason_codes": ["signal_not_measured"] if prediction_missing else [],
        }
        if name == "ece":
            expected.update(bins=1, min_bin_n=1)
        assert result["calibration"][name] == expected
    missing = binary_missing or prediction_missing
    assert result["verdict"] == ("not_measured" if missing else "pass")
    assert result["score"] == (None if missing else 1)
    assert result["escalate"] is missing
    if missing:
        signal = result["signals"][method]
        assert signal == {
            "method": "beta_mixture_eprocess" if method == 0 else "calibration",
            "instrument": "binary" if method == 0 else "predictions",
            "availability": "not_measured",
            "verdict": None,
            "score": None,
            "confidence": 0,
            "e_value": None,
            "null_id": None,
            "reason_codes": ["signal_not_measured"],
        }
        assert "required_instrument_missing" in result["reason_codes"]


@pytest.mark.parametrize("method", [0, 1])
@pytest.mark.parametrize("loss", ["missing", "error"])
def test_declared_losses_preserve_population_and_cause(
    inputs: Inputs, method: int, loss: str
) -> None:
    observation, policy = streams(inputs)
    reading = observation["payload"]["readings"][method]
    row = reading["rows"].pop(0)
    observation["payload"]["availability"] = [
        {
            "case_id": row["case_id"],
            "instrument": reading["instrument"],
            "metric_id": reading["metric_id"],
            "epoch": None,
            "repeat": 0,
            "availability": loss,
        }
    ]
    result = run(observation, policy)
    assert result["verdict"] == "not_measured"
    assert result["availability"]["state"] == "partial"
    assert result["availability"]["missing"] == [reading["instrument"]]
    assert result["availability"]["available"] == ["predictions" if method == 0 else "binary"]
    assert f"required_instrument_{loss}" in result["reason_codes"]
    assert result["signals"][method]["confidence"] == 0
    population = len(reading["rows"]) + 1
    assert all(
        result["calibration"][name]["n"] == population for name in ("brier", "ece", "pass_k")
    )


def test_empty_population_projects_complete_missing_views(inputs: Inputs) -> None:
    observation, policy = streams(inputs, count=0)
    result = run(observation, policy)
    for name in ("brier", "ece", "pass_k"):
        expected: JsonObject = {
            "availability": "not_measured",
            "value": None,
            "n": 0,
            "reason_codes": ["no_observations"],
        }
        expected.update(
            {"bins": 1, "min_bin_n": 1} if name == "ece" else {"k": 1} if name == "pass_k" else {}
        )
        assert result["calibration"][name] == expected
    assert result["reason_codes"] == [
        "no_observations",
        "signal_not_measured",
        "below_quorum",
        "required_instrument_missing",
    ]
    assert result["score"] is None and result["escalate"] is True


@pytest.mark.parametrize("stop", ["threshold", "max_observations"])
def test_pass_k_uses_declared_population_after_stream_stops(inputs: Inputs, stop: str) -> None:
    observation, policy = streams(inputs, count=10)
    rows = observation["payload"]["readings"][0]["rows"]
    rows[8]["outcome"] = rows[9]["outcome"] = 0
    policy["calibration"]["k"] = 2
    if stop == "max_observations":
        policy["eprocess"]["max_observations"] = 3
    result = run(observation, policy)
    assert result["eprocess"]["observations_used"] == (8 if stop == "threshold" else 3)
    assert result["calibration"]["pass_k"] == {
        "availability": "measured",
        "value": math.comb(8, 2) / math.comb(10, 2),
        "n": 10,
        "k": 2,
        "reason_codes": [],
    }


@pytest.mark.parametrize("argument", ["observation_digest", "policy_digest"])
def test_each_digest_has_public_diagnostic(inputs: Inputs, argument: str) -> None:
    observation, policy = inputs
    digests = {"observation_digest": DIGEST, "policy_digest": DIGEST}
    assert assemble_read(observation, policy, **digests)["sources"] == {
        "observations": [DIGEST],
        "policy": DIGEST,
    }
    digests[argument] = "sha256:" + "A" * 64
    with pytest.raises(ReadInputError) as caught:
        assemble_read(observation, policy, **digests)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_POLICY",
        "INVALID_DIGEST",
        "/policy",
    )


@pytest.mark.parametrize("cost,reason", [(None, "COST_REQUIRED"), (2, "NO_TRIALS")])
def test_engine_budget_errors_have_public_diagnostics(
    inputs: Inputs, cost: int | None, reason: str
) -> None:
    observation, policy = streams(inputs)
    policy["eprocess"]["max_cost"] = 1
    for row in observation["payload"]["readings"][0]["rows"]:
        row["payload"]["cost"] = cost
    with pytest.raises(ReadInputError) as caught:
        run(observation, policy)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_POLICY",
        reason,
        "/policy",
    )
    for row in observation["payload"]["readings"][0]["rows"]:
        row["payload"]["cost"] = 0.1
    expected_trials = len(observation["payload"]["readings"][0]["rows"])
    assert run(observation, policy)["eprocess"]["observations_used"] == expected_trials


def test_source_claims_are_ignored_without_mutation(inputs: Inputs) -> None:
    observation, policy = streams(inputs)
    baseline = run(observation, policy)
    observation["payload"]["read"] = {"verdict": "fail", "score": 0, "e_value": 1e99}
    before = copy.deepcopy((observation, policy))
    assert run(observation, policy) == baseline
    assert (observation, policy) == before


@pytest.mark.parametrize("error_method", [0, 1])
def test_shared_instrument_error_takes_precedence_over_missing(
    inputs: Inputs, error_method: int
) -> None:
    observation, policy = streams(inputs)
    policy["required_instruments"] = ["shared"]
    for method in ("eprocess", "calibration"):
        policy["inputs"][method]["instrument"] = "shared"
    losses: list[JsonObject] = []
    for index, reading in enumerate(observation["payload"]["readings"]):
        reading["instrument"] = "shared"
        row = reading["rows"].pop(0)
        losses.append(
            {
                "case_id": row["case_id"],
                "instrument": "shared",
                "metric_id": reading["metric_id"],
                "epoch": None,
                "repeat": 0,
                "availability": "error" if index == error_method else "missing",
            }
        )
    observation["payload"]["availability"] = losses
    result = run(observation, policy)
    assert result["availability"] == {
        "required_instruments": ["shared"],
        "quorum": 1,
        "available": [],
        "missing": ["shared"],
        "state": "below_quorum",
        "triggered": True,
    }
    assert result["reason_codes"] == [
        "signal_not_measured",
        "below_quorum",
        "required_instrument_error",
    ]
    assert result["score"] is None and result["escalate"] is True


def test_calibration_floors_project_causes_without_erasing_other_views(inputs: Inputs) -> None:
    observation, policy = streams(inputs)
    policy["min_bin_n"] = 9
    policy["calibration"]["k"] = 9
    result = run(observation, policy)
    assert result["calibration"] == {
        "brier": {"availability": "measured", "value": 0, "n": 8, "reason_codes": []},
        "ece": {
            "availability": "not_measured",
            "value": None,
            "n": 8,
            "reason_codes": ["bin_below_min_n"],
            "bins": 1,
            "min_bin_n": 9,
        },
        "pass_k": {
            "availability": "not_measured",
            "value": None,
            "n": 8,
            "reason_codes": ["n_below_k"],
            "k": 9,
        },
    }
    assert result["signals"][1] == {
        "method": "calibration",
        "instrument": "predictions",
        "availability": "not_measured",
        "verdict": None,
        "score": None,
        "confidence": 0,
        "e_value": None,
        "null_id": None,
        "reason_codes": ["bin_below_min_n"],
    }
    assert result["verdict"] == "not_measured"
    assert result["reason_codes"] == [
        "bin_below_min_n",
        "signal_not_measured",
        "required_instrument_missing",
    ]
    policy["min_bin_n"] = policy["calibration"]["k"] = 8
    measured = run(observation, policy)
    assert measured["verdict"] == "pass"
    assert measured["calibration"]["pass_k"]["value"] == 1
