"""Event construct, inventory and diagnostic eligibility regression controls."""

from __future__ import annotations

from typing import Any

import pytest
from _event_acceptance import inputs

from underwrite.measurement.event_read import EventReadError, assemble_event_read
from underwrite.measurement.read_input import ReadInputError
from underwrite.measurement_contract.semantics import (
    ASSUMPTIONS,
    parse_semantics,
    semantic_digest,
)


def measure(observation: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    policy["semantic_digest"] = semantic_digest(
        parse_semantics(observation["payload"]["measurement_semantics"])
    )
    return assemble_event_read(
        observation,
        policy,
        observation_digest="sha256:" + "0" * 64,
        policy_digest="sha256:" + "1" * 64,
    )


@pytest.mark.parametrize(
    ("label", "direction", "verdict"),
    [(0, "higher", "fail"), (1, "higher", "pass"), (0, "lower", "pass"), (1, "lower", "fail")],
)
def test_direction_controls_verdict_not_perfect_forecasts(
    label: int, direction: str, verdict: str
) -> None:
    obs, policy = inputs([label] * 8)
    obs["payload"]["measurement_semantics"]["desirable_direction"] = direction
    result = measure(obs, policy)
    assert result["verdict"] == verdict
    assert result["diagnostics"]["brier"]["value"] == 0
    assert "score" not in result and "confidence" not in result


@pytest.mark.parametrize("unknown", [True, False])
def test_withheld_wealth_when_assumptions_or_population_missing(unknown: bool) -> None:
    obs, policy = inputs([1] * 8)
    if unknown:
        policy["assumptions"][ASSUMPTIONS[0]] = "unknown"
    else:
        obs["payload"]["readings"][0]["rows"].pop()
    result = measure(obs, policy)
    assert result["verdict"] == "not_measured"
    assert result["directional"]["e_upper"] is None
    assert result["directional"]["consumed_count"] == 0
    assert result["diagnostics"]["event_rate"]["value"] == 1


def test_outcome_only_score_not_interpreted_as_probability() -> None:
    obs, policy = inputs([1] * 8, prediction=False)
    for row in obs["payload"]["readings"][0]["rows"]:
        row["score"] = "grade A"
    result = measure(obs, policy)
    assert result["verdict"] == "pass"
    assert result["population"]["predictions"]["observed_count"] is None
    assert result["diagnostics"]["brier"]["reason_codes"] == ["prediction_semantics_unavailable"]


def test_unknown_timing_withholds_calibration_only() -> None:
    obs, policy = inputs([1] * 8)
    obs["payload"]["measurement_semantics"]["prediction"]["timing"] = "unknown"
    result = measure(obs, policy)
    assert result["verdict"] == "pass"
    assert result["diagnostics"]["ece"]["value"] is None
    assert sum(result["diagnostics"]["ece"]["bin_counts"]) == len(policy["expected_case_ids"])


def test_all_k_is_all_success_subset_not_any_success() -> None:
    result = measure(*inputs([1, 1, 0, 0]))
    assert result["diagnostics"]["all_k_success"]["value"] == pytest.approx(1 / 6)
    assert result["diagnostics"]["all_k_success"]["estimand"] == "all_k_satisfied"


@pytest.mark.parametrize("mutation", ["extra", "duplicate", "reorder", "boolean", "probability"])
def test_invalid_selected_inputs_refused(mutation: str) -> None:
    obs, policy = inputs([1] * 8)
    rows = obs["payload"]["readings"][0]["rows"]
    if mutation == "extra":
        rows[-1]["case_id"] = "extra"
    elif mutation == "duplicate":
        rows[-1]["case_id"] = rows[0]["case_id"]
    elif mutation == "reorder":
        rows[0]["case_id"], rows[1]["case_id"] = rows[1]["case_id"], rows[0]["case_id"]
    elif mutation == "boolean":
        rows[0]["outcome"] = True
    else:
        obs["payload"]["readings"][1]["rows"][0]["score"] = 2
    with pytest.raises((EventReadError, ReadInputError)):
        measure(obs, policy)


def test_error_missing_and_absent_ids_conserve_population() -> None:
    obs, policy = inputs([1] * 8)
    obs["payload"]["readings"][0]["rows"] = obs["payload"]["readings"][0]["rows"][:5]
    obs["payload"]["availability"] = [
        {
            "instrument": "label",
            "metric_id": "event",
            "case_id": "case-5",
            "repeat": 0,
            "availability": "error",
        }
    ]
    result = measure(obs, policy)
    assert result["population"]["outcomes"] == {
        "observed_count": 5,
        "missing_ids": ["case-6", "case-7"],
        "error_ids": ["case-5"],
    }
    assert result["diagnostics"]["all_k_success"]["value"] is None
    assert result["diagnostics"]["brier"]["n"] == len(obs["payload"]["readings"][0]["rows"])


def test_ece_occupied_bin_minimum_never_votes() -> None:
    obs, policy = inputs([1] * 8)
    obs["payload"]["readings"][1]["rows"][0]["score"] = 0
    result = measure(obs, policy)
    assert result["verdict"] == "pass"
    assert result["diagnostics"]["ece"]["reason_codes"] == ["bin_below_min_n"]
    assert result["diagnostics"]["brier"]["availability"] == "measured"


def test_zero_outcomes_retains_unknown_not_zero_event_rate() -> None:
    obs, policy = inputs([0, 1])
    obs["payload"]["readings"][0]["rows"] = []
    result = measure(obs, policy)
    assert result["diagnostics"]["event_rate"]["value"] is None
    assert result["diagnostics"]["event_rate"]["reason_codes"] == ["no_observed_outcomes"]
    assert result["directional"]["e_lower"] is None


def test_complete_population_below_k_keeps_descriptive_counts() -> None:
    obs, policy = inputs([1])
    result = measure(obs, policy)
    assert result["diagnostics"]["all_k_success"]["reason_codes"] == ["n_below_k"]
    assert result["diagnostics"]["all_k_success"]["n"] == 1


@pytest.mark.parametrize("field", ["semantic_digest", "schema", "profile", "extra"])
def test_policy_contract_errors_are_owned(field: str) -> None:
    obs, policy = inputs([1] * 8)
    policy[field] = "measurement_policy.v2" if field == "schema" else "invalid"
    with pytest.raises(EventReadError) as caught:
        assemble_event_read(
            obs, policy, observation_digest="sha256:" + "0" * 64, policy_digest="sha256:" + "1" * 64
        )
    assert caught.value.code in ("INVALID_POLICY", "UNSUPPORTED_PROFILE")


def test_forecast_incomplete_and_timing_unknown_both_retained() -> None:
    obs, policy = inputs([1] * 8)
    obs["payload"]["measurement_semantics"]["prediction"]["timing"] = "unknown"
    obs["payload"]["readings"][1]["rows"].pop()
    result = measure(obs, policy)
    assert result["diagnostics"]["brier"]["reason_codes"] == [
        "forecast_timing_unknown",
        "paired_population_incomplete",
    ]
    assert result["verdict"] == "pass"


def test_inventory_must_be_nonempty_and_unique() -> None:
    obs, policy = inputs([1])
    for inventory in ([], ["case-0", "case-0"]):
        policy["expected_case_ids"] = inventory
        with pytest.raises(EventReadError, match="INVALID_POLICY_FIELDS"):
            measure(obs, policy)


@pytest.mark.parametrize(
    ("schema", "profile", "code", "reason"),
    [
        ("measurement_policy.v1", "wrong", "INVALID_POLICY", "LEGACY_POLICY_REQUIRES_MIGRATION"),
        ("measurement_policy.v2", "wrong", "INVALID_POLICY", "LEGACY_POLICY_REQUIRES_MIGRATION"),
        ("measurement_policy.v3", "wrong", "UNSUPPORTED_PROFILE", "UNSUPPORTED_POLICY_PROFILE"),
        (
            "measurement_policy.v3",
            "binary-event-directional.v1",
            "INVALID_POLICY",
            "INVALID_POLICY_FIELDS",
        ),
    ],
)
def test_policy_validation_preserves_error_surface(
    schema: str, profile: str, code: str, reason: str
) -> None:
    observation, _ = inputs([1])
    with pytest.raises(EventReadError) as caught:
        assemble_event_read(
            observation,
            {"schema": schema, "profile": profile},
            observation_digest="sha256:" + "0" * 64,
            policy_digest="sha256:" + "1" * 64,
        )
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        code,
        reason,
        "/policy",
    )
