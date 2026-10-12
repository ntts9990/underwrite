"""Card: measurement-semantics | Paper: ADR-020 | Stage: semantic-repair.

Closed declarations of measured meaning; declarations are not verification.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, cast

from underwrite_core.canonical import canonical_bytes, tagged_digest

PROFILE = "binary-event-directional.v1"
MAX_CASES = 2000
MAX_IDENTITY = 128
MAX_TEXT = 1024
ALPHA_TOTAL = 0.05
SIDE_ALPHA = 0.025
THRESHOLD = 40
ASSUMPTIONS = (
    "conditional_mean_model",
    "parameters_prespecified",
    "order_prespecified",
    "outcome_independent_selection",
)
CONTRACT_REASONS = (
    "INVALID_SHAPE",
    "INVALID_VALUE",
    "SEMANTIC_DIGEST_MISMATCH",
    "INCONSISTENT_READ",
)
DECISION_REASONS = (
    "assumptions_not_declared",
    "outcome_population_incomplete",
    "conflicting_directional_crossing",
    "favorable_threshold_crossed",
    "unfavorable_threshold_crossed",
    "threshold_not_reached",
    "observation_budget_exhausted",
)
DIAGNOSTIC_REASONS = (
    "prediction_semantics_unavailable",
    "forecast_timing_unknown",
    "paired_population_incomplete",
    "no_observed_outcomes",
    "bin_below_min_n",
    "n_below_k",
    "outcome_population_incomplete",
)
LIMITATIONS = (
    "source_claims_not_independently_verified",
    "semantic_digest_binds_declaration_only",
    "assumption_statuses_not_independently_verified",
    "sampling_representativeness_not_established",
    "label_truth_not_established",
    "finite_sample_binning_diagnostic",
    "all_k_is_case_subset_not_per_task_repeat",
    "no_cross_run_multiplicity_control",
    "no_posterior_correctness_probability",
    "no_merge_or_deployment_authority",
)
FORECAST_LIMITATION = "forecast_probability_meaning_declared_not_verified"
STATUS_VERDICTS = {
    "favorable": "pass",
    "unfavorable": "fail",
    "inconclusive": "warn",
    "withheld": "not_measured",
}


class ContractError(ValueError):
    def __init__(self, reason: str) -> None:
        if reason not in CONTRACT_REASONS:
            raise ValueError("Unknown contract reason")
        self.reason = reason
        super().__init__(reason)


def require(condition: bool, reason: str = "INVALID_VALUE") -> None:
    if not condition:
        raise ContractError(reason)


def closed(value: object, fields: str) -> dict[str, Any]:
    require(isinstance(value, dict), "INVALID_SHAPE")
    result = cast(dict[str, Any], value)
    require(set(result) == set(fields.split()), "INVALID_SHAPE")
    return result


def text(value: object, maximum: int = MAX_IDENTITY) -> str:
    require(isinstance(value, str))
    result = cast(str, value)
    require(
        bool(result.strip())
        and len(result) <= maximum
        and unicodedata.normalize("NFC", result) == result
    )
    try:
        result.encode("utf-8")
    except UnicodeEncodeError:
        raise ContractError("INVALID_VALUE") from None
    return result


def integer(value: object, minimum: int = 0, maximum: int = MAX_CASES) -> int:
    require(type(value) is int)
    result = cast(int, value)
    require(minimum <= result <= maximum)
    return result


def number(value: object, minimum: float = 0, maximum: float = math.inf) -> float:
    require(type(value) in (int, float))
    try:
        result = float(cast(float, value))
    except OverflowError:
        raise ContractError("INVALID_VALUE") from None
    require(math.isfinite(result) and minimum <= result <= maximum)
    return result


def digest(value: object) -> str:
    result = text(value)
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", result) is not None)
    return result


@dataclass(frozen=True)
class MeasurementSemantics:
    canonical: bytes


def parse_semantics(value: object) -> MeasurementSemantics:
    obj = closed(
        value,
        "schema profile construct event unit sampling_unit desirable_direction "
        "outcome prediction population",
    )
    require(obj["schema"] == "measurement_semantics.v1" and obj["profile"] == PROFILE)
    require(obj["unit"] == "proportion" and obj["sampling_unit"] == "case")
    require(obj["desirable_direction"] in ("higher", "lower"))
    construct = closed(obj["construct"], "id definition")
    text(construct["id"])
    text(construct["definition"], MAX_TEXT)
    event = closed(obj["event"], "id positive_label_definition")
    text(event["id"])
    text(event["positive_label_definition"], MAX_TEXT)
    outcome = closed(obj["outcome"], "instrument metric_id label_source rubric")
    text(outcome["instrument"])
    text(outcome["metric_id"])
    for key in ("label_source", "rubric"):
        owner = closed(outcome[key], "id version reference")
        text(owner["id"])
        text(owner["version"])
        text(owner["reference"], MAX_TEXT)
    require(isinstance(obj["prediction"], dict), "INVALID_SHAPE")
    prediction = cast(dict[str, Any], obj["prediction"])
    if prediction.get("status") == "unavailable":
        closed(prediction, "status")
    else:
        closed(prediction, "status instrument metric_id event_id timing reference")
        require(prediction["status"] == "declared_probability_of_event")
        text(prediction["instrument"])
        text(prediction["metric_id"])
        require(prediction["event_id"] == event["id"])
        require(prediction["timing"] in ("before_outcome", "unknown"))
        text(prediction["reference"], MAX_TEXT)
    population = closed(obj["population"], "frame_id description")
    text(population["frame_id"])
    text(population["description"], MAX_TEXT)
    return MeasurementSemantics(canonical_bytes(obj))


def project_semantics(value: MeasurementSemantics) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(value.canonical))


def semantic_digest(value: MeasurementSemantics) -> str:
    return tagged_digest("underwrite.measurement_semantics.v1", value.canonical)
