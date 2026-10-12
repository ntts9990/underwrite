"""Pure semantic identity and supplied-calculation contract regression controls."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from underwrite.measurement_contract.read import validate_event_read
from underwrite.measurement_contract.semantics import (
    ASSUMPTIONS,
    FORECAST_LIMITATION,
    LIMITATIONS,
    PROFILE,
    ContractError,
    parse_semantics,
    project_semantics,
    semantic_digest,
)


def semantics() -> dict[str, Any]:
    owner = {"id": "synthetic", "version": "1", "reference": "synthetic example"}
    return {
        "schema": "measurement_semantics.v1",
        "profile": PROFILE,
        "construct": {"id": "success", "definition": "Synthetic event frequency"},
        "event": {"id": "success", "positive_label_definition": "One means event occurred"},
        "unit": "proportion",
        "sampling_unit": "case",
        "desirable_direction": "higher",
        "outcome": {
            "instrument": "labels",
            "metric_id": "event",
            "label_source": owner,
            "rubric": owner,
        },
        "prediction": {"status": "unavailable"},
        "population": {"frame_id": "synthetic", "description": "Two synthetic cases"},
    }


def read() -> dict[str, Any]:
    sem = semantics()
    unavailable = {
        "availability": "not_measured",
        "value": None,
        "n": 0,
        "reason_codes": ["prediction_semantics_unavailable"],
    }
    return {
        "schema": "read.v2",
        "profile": PROFILE,
        "scope": "aggregate",
        "subject": {"subject_id": "synthetic", "conditions": {}},
        "semantics": sem,
        "semantic_digest": semantic_digest(parse_semantics(sem)),
        "sources": {"observations": ["sha256:" + "0" * 64], "policy": "sha256:" + "1" * 64},
        "population": {
            "expected": {"count": 2, "ordered_inventory_digest": "sha256:" + "2" * 64},
            "outcomes": {"observed_count": 2, "missing_ids": [], "error_ids": []},
            "predictions": {
                "status": "unavailable",
                "observed_count": None,
                "missing_ids": None,
                "error_ids": None,
            },
        },
        "directional": {
            "method": "beta-mixture-two-direction.v1",
            "availability": "measured",
            "p0": 0.5,
            "alpha_total": 0.05,
            "side_alpha": 0.025,
            "threshold": 40,
            "prior_a": 1,
            "prior_b": 1,
            "max_observations": None,
            "desirable_direction": "higher",
            "error_control": {
                "method": "bonferroni_equal_two_sides",
                "upper_null": "conditional_mean_lte_p0",
                "lower_null": "conditional_mean_gte_p0",
                "per_side_alpha": 0.025,
                "point_null_union_alpha": 0.05,
            },
            "assumptions": dict.fromkeys(ASSUMPTIONS, "declared"),
            "e_upper": 1,
            "e_lower": 1,
            "consumed_count": 2,
            "successes": 1,
            "failures": 1,
            "unused_observed_count": 0,
            "stop_reason": "end_of_population",
        },
        "diagnostics": {
            "event_rate": {
                "method": "observed-event-fraction.v1",
                "unit": "proportion",
                "scope": "observed_expected_cases",
                "availability": "measured",
                "value": 0.5,
                "n": 2,
                "positive_count": 1,
                "reason_codes": [],
            },
            "brier": {
                "method": "brier-mean-squared-error.v1",
                "unit": "squared_probability",
                **unavailable,
            },
            "ece": {
                "method": "fixed-width-absolute-gap.v1",
                "unit": "probability",
                **unavailable,
                "bins": 2,
                "min_bin_n": 2,
                "bin_counts": [0, 0],
            },
            "all_k_success": {
                "method": "uniform-subset-all-success.v1",
                "unit": "fraction_of_k_subsets",
                "estimand": "all_k_satisfied",
                "availability": "measured",
                "value": 0,
                "n": 2,
                "c": 1,
                "k": 2,
                "reason_codes": [],
            },
        },
        "decision": {"status": "inconclusive"},
        "verdict": "warn",
        "reason_codes": ["threshold_not_reached"],
        "limitations": list(LIMITATIONS),
    }


def test_semantics_are_immutable_defensive_and_meaning_bound() -> None:
    raw = semantics()
    parsed = parse_semantics(raw)
    original = semantic_digest(parsed)
    raw["construct"]["definition"] = "different"
    assert semantic_digest(parsed) == original
    projection = project_semantics(parsed)
    projection["construct"]["definition"] = "another"
    assert semantic_digest(parsed) == original
    assert semantic_digest(parse_semantics(raw)) != original


@pytest.mark.parametrize("field,value", [("verified", True), ("confidence", 0.99)])
def test_unknown_declaration_fields_refused(field: str, value: object) -> None:
    raw = semantics()
    raw[field] = value
    with pytest.raises(ContractError):
        parse_semantics(raw)


def test_closed_read_binds_subject_and_declared_meaning() -> None:
    raw = read()
    binding = validate_event_read(raw)
    assert binding.verdict == "warn" and binding.availability == "full"
    assert binding.semantic_digest == raw["semantic_digest"]
    raw["subject"]["subject_id"] = "renamed"
    renamed = validate_event_read(raw)
    assert renamed.semantic_digest == binding.semantic_digest
    assert renamed.read_digest != binding.read_digest


@pytest.mark.parametrize(
    "path,value",
    [
        (("confidence",), 0.99),
        (("verdict",), "pass"),
        (("semantic_digest",), "sha256:" + "a" * 64),
        (("population", "predictions", "observed_count"), 0),
        (("directional", "consumed_count"), True),
        (("directional", "e_upper"), float("inf")),
        (("directional", "assumptions", "parameters_prespecified"), "unknown"),
        (("diagnostics", "all_k_success", "c"), 2),
        (("diagnostics", "ece", "bin_counts"), [1, 0]),
        (("diagnostics", "event_rate", "value"), 1),
        (("limitations",), []),
    ],
)
def test_inconsistent_supplied_reads_refuse(path: tuple[str, ...], value: object) -> None:
    raw = read()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ContractError):
        validate_event_read(raw)


def test_favorable_and_unfavorable_sides_cannot_be_swapped() -> None:
    raw = read()
    raw["directional"].update(e_upper=40, stop_reason="upper_crossing")
    raw.update(
        decision={"status": "favorable"},
        verdict="pass",
        reason_codes=["favorable_threshold_crossed"],
    )
    assert validate_event_read(raw).verdict == "pass"
    raw["semantics"]["desirable_direction"] = "lower"
    raw["semantic_digest"] = semantic_digest(parse_semantics(raw["semantics"]))
    raw["directional"]["desirable_direction"] = "lower"
    with pytest.raises(ContractError):
        validate_event_read(raw)
    raw.update(
        decision={"status": "unfavorable"},
        verdict="fail",
        reason_codes=["unfavorable_threshold_crossed"],
    )
    assert validate_event_read(raw).verdict == "fail"


def test_unknown_assumptions_withhold_numeric_wealths() -> None:
    raw = read()
    raw["directional"]["assumptions"]["parameters_prespecified"] = "unknown"
    raw["directional"].update(
        availability="not_measured",
        e_upper=None,
        e_lower=None,
        consumed_count=0,
        successes=0,
        failures=0,
        unused_observed_count=2,
        stop_reason="withheld",
    )
    raw.update(
        decision={"status": "withheld"},
        verdict="not_measured",
        reason_codes=["assumptions_not_declared"],
    )
    assert validate_event_read(raw).verdict == "not_measured"
    raw["directional"]["e_upper"] = 0
    with pytest.raises(ContractError):
        validate_event_read(raw)


@pytest.mark.parametrize("positive", [0, 2])
def test_directional_prefix_cannot_exceed_full_population_counts(positive: int) -> None:
    raw = read()
    total = raw["population"]["outcomes"]["observed_count"]
    raw["diagnostics"]["event_rate"].update(positive_count=positive, value=positive / total)
    raw["diagnostics"]["all_k_success"].update(c=positive, value=int(positive == total))
    with pytest.raises(ContractError, match="INCONSISTENT_READ"):
        validate_event_read(raw)


def test_directional_prefix_may_be_smaller_than_full_population_counts() -> None:
    raw = read()
    raw["directional"].update(
        consumed_count=1,
        successes=1,
        failures=0,
        unused_observed_count=1,
        max_observations=1,
        stop_reason="budget_observations",
    )
    raw["reason_codes"] = ["observation_budget_exhausted"]
    assert validate_event_read(raw).verdict == "warn"


def test_declared_forecasts_require_timing_and_complete_pairs() -> None:
    raw = read()
    raw["semantics"]["prediction"] = {
        "status": "declared_probability_of_event",
        "instrument": "forecast",
        "metric_id": "probability",
        "event_id": "success",
        "timing": "unknown",
        "reference": "synthetic",
    }
    raw["semantic_digest"] = semantic_digest(parse_semantics(raw["semantics"]))
    raw["population"]["predictions"] = {
        "status": "declared",
        "observed_count": 1,
        "missing_ids": ["missing"],
        "error_ids": [],
    }
    for name in ("brier", "ece"):
        raw["diagnostics"][name].update(
            n=1, reason_codes=["forecast_timing_unknown", "paired_population_incomplete"]
        )
    raw["diagnostics"]["ece"]["bin_counts"] = [1, 0]
    raw["limitations"].append(FORECAST_LIMITATION)
    assert validate_event_read(raw).verdict == "warn"
    altered = deepcopy(raw)
    altered["diagnostics"]["brier"].update(availability="measured", value=0, reason_codes=[])
    with pytest.raises(ContractError):
        validate_event_read(altered)


def test_missing_outcomes_keep_subset_diagnostic_but_withhold_population_and_inference() -> None:
    raw = read()
    raw["population"]["outcomes"].update(observed_count=1, missing_ids=["case-missing"])
    raw["directional"].update(
        availability="not_measured",
        e_upper=None,
        e_lower=None,
        consumed_count=0,
        successes=0,
        failures=0,
        unused_observed_count=1,
        stop_reason="withheld",
    )
    raw["diagnostics"]["event_rate"].update(n=1, positive_count=1, value=1)
    raw["diagnostics"]["all_k_success"].update(
        availability="not_measured",
        n=1,
        value=None,
        reason_codes=["outcome_population_incomplete"],
    )
    raw.update(
        decision={"status": "withheld"},
        verdict="not_measured",
        reason_codes=["outcome_population_incomplete"],
    )
    assert validate_event_read(raw).availability == "partial"
    raw["population"]["outcomes"]["error_ids"] = ["case-missing"]
    with pytest.raises(ContractError):
        validate_event_read(raw)


def test_conflicting_crossings_are_withheld_but_arithmetic_is_measured() -> None:
    raw = read()
    raw["directional"].update(e_upper=40, e_lower=40, stop_reason="conflicting_crossing")
    raw.update(
        decision={"status": "withheld"},
        verdict="not_measured",
        reason_codes=["conflicting_directional_crossing"],
    )
    assert validate_event_read(raw).verdict == "not_measured"
    raw["directional"]["availability"] = "not_measured"
    with pytest.raises(ContractError):
        validate_event_read(raw)


def test_occupied_ece_bins_only_are_subject_to_minimum() -> None:
    raw = read()
    raw["semantics"]["prediction"] = {
        "status": "declared_probability_of_event",
        "instrument": "forecast",
        "metric_id": "probability",
        "event_id": "success",
        "timing": "before_outcome",
        "reference": "synthetic",
    }
    raw["semantic_digest"] = semantic_digest(parse_semantics(raw["semantics"]))
    raw["population"]["predictions"] = {
        "status": "declared",
        "observed_count": 2,
        "missing_ids": [],
        "error_ids": [],
    }
    for name in ("brier", "ece"):
        raw["diagnostics"][name].update(n=2, availability="measured", value=0.25, reason_codes=[])
    raw["diagnostics"]["ece"]["bin_counts"] = [2, 0]
    raw["limitations"].append(FORECAST_LIMITATION)
    assert validate_event_read(raw).verdict == "warn"
    raw["diagnostics"]["ece"]["bin_counts"] = [1, 1]
    with pytest.raises(ContractError):
        validate_event_read(raw)
    raw["diagnostics"]["ece"].update(
        availability="not_measured",
        value=None,
        reason_codes=["bin_below_min_n"],
    )
    assert validate_event_read(raw).verdict == "warn"


@pytest.mark.parametrize("identity", [" ", "e\u0301", "\ud800", "x" * 129])
def test_semantic_identity_is_bounded_utf8_and_nfc(identity: str) -> None:
    raw = semantics()
    raw["construct"]["id"] = identity
    with pytest.raises(ContractError):
        parse_semantics(raw)
