"""Card: measurement-semantics | Paper: ADR-020 | Stage: semantic-repair.

Compose declared event evidence without turning calibration into a quality vote.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from underwrite_core.canonical import canonical_bytes, tagged_digest

from underwrite.measurement import calibration, eprocess, read_input
from underwrite.measurement.directional import run_directional
from underwrite.measurement.event_policy import EventPolicyError, validate_event_policy
from underwrite.measurement_contract import semantics as contract
from underwrite.measurement_contract.read import crossing_status, validate_event_read

EventReason = Literal[
    "INVALID_POLICY_FIELDS",
    "LEGACY_POLICY_REQUIRES_MIGRATION",
    "UNSUPPORTED_POLICY_PROFILE",
    "INVALID_SEMANTICS",
    "SEMANTIC_DIGEST_MISMATCH",
    "UNEXPECTED_CASE",
    "INVENTORY_ORDER_MISMATCH",
    "INVALID_READ_BINDING",
    "INVALID_DIGEST",
    "E_VALUE_OVERFLOW",
]
EVENT_READ_REASONS = frozenset(get_args(EventReason))


class EventReadError(ValueError):
    """Finite input diagnostics without source text."""

    def __init__(self, code: str, reason: EventReason, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _policy(value: read_input.JSON) -> dict[str, Any]:
    try:
        return validate_event_policy(value)
    except EventPolicyError as exc:
        raise EventReadError(exc.code, exc.reason, exc.location) from None


def _role_slot(row: read_input.JSON, state: str | None, *, prediction: bool) -> read_input.Slot:
    value = row["score" if prediction else "outcome"]
    if value is not None:
        if prediction:
            read_input.number(value, 0, 1, "/observation")
        else:
            read_input.integer(value, 0, 1, "/observation")
    read_input.require(
        state not in ("error", "missing") or value is None, "CONTRADICTORY_AVAILABILITY"
    )
    return read_input.Slot(
        None if prediction else value,
        value if prediction else None,
        None,
        state if state in ("error", "missing") else None,
    )


def _select(
    payload: read_input.JSON, declaration: dict[str, Any], ids: list[str], *, prediction: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    selector = {key: declaration[key] for key in ("instrument", "metric_id")}
    slots, _, order = read_input.select_slots(
        payload, selector, decode=lambda row, state: _role_slot(row, state, prediction=prediction)
    )
    if set(slots) - set(ids):
        raise EventReadError("INVALID_OBSERVATION", "UNEXPECTED_CASE", "/observation")
    rank = {key: index for index, key in enumerate(ids)}
    if order != sorted(order, key=rank.__getitem__):
        raise EventReadError("INVALID_OBSERVATION", "INVENTORY_ORDER_MISMATCH", "/observation")
    values: dict[str, Any] = {}
    missing: list[str] = []
    errors: list[str] = []
    for key in ids:
        slot = slots.get(key)
        value = None if slot is None else slot.prediction if prediction else slot.outcome
        if slot is not None and slot.loss == "error":
            errors.append(key)
        elif value is None:
            missing.append(key)
        else:
            values[key] = value
    return values, {"observed_count": len(values), "missing_ids": missing, "error_ids": errors}


def _diagnostic(
    method: str, unit: str, n: int, value: float | None, reasons: list[str]
) -> dict[str, Any]:
    return {
        "method": method,
        "unit": unit,
        "availability": "not_measured" if reasons else "measured",
        "value": value,
        "n": n,
        "reason_codes": reasons,
    }


def _diagnostics(
    outcomes: dict[str, Any], predictions: dict[str, Any], sem: dict[str, Any], p: dict[str, Any]
) -> dict[str, Any]:
    n, c = len(outcomes), sum(outcomes.values())
    complete = n == len(p["expected_case_ids"])
    options = p["diagnostics"]
    event = _diagnostic(
        "observed-event-fraction.v1",
        "proportion",
        n,
        c / n if n else None,
        [] if n else ["no_observed_outcomes"],
    )
    event.update(scope="observed_expected_cases", positive_count=c)
    all_k = calibration.pass_k(c, n, options["all_k"])
    reasons = list(all_k.reason_codes) if complete else ["outcome_population_incomplete"]
    subset = _diagnostic(
        "uniform-subset-all-success.v1",
        "fraction_of_k_subsets",
        n,
        None if reasons else all_k.value,
        reasons,
    )
    subset.update(estimand="all_k_satisfied", c=c, k=all_k.k)
    pairs = [
        calibration.make_pair(predictions[key], value)
        for key, value in outcomes.items()
        if key in predictions
    ]
    reasons: list[str] = []
    if sem["prediction"]["status"] == "unavailable":
        reasons = ["prediction_semantics_unavailable"]
    else:
        if sem["prediction"]["timing"] == "unknown":
            reasons.append("forecast_timing_unknown")
        if len(pairs) != len(p["expected_case_ids"]):
            reasons.append("paired_population_incomplete")
    bins, minimum = options["ece_bins"], options["min_bin_n"]
    counts = [0] * bins
    for pair in pairs:
        counts[calibration.bin_index(pair.prediction, bins)] += 1
    brier_value = None if reasons else calibration.brier(pairs).value
    ece_value = None
    ece_reasons = reasons.copy()
    if not reasons:
        measured = calibration.ece(pairs, bins, minimum)
        ece_value, ece_reasons = measured.value, list(measured.reason_codes)
    brier = _diagnostic(
        "brier-mean-squared-error.v1", "squared_probability", len(pairs), brier_value, reasons
    )
    ece = _diagnostic(
        "fixed-width-absolute-gap.v1", "probability", len(pairs), ece_value, ece_reasons
    )
    ece.update(bins=bins, min_bin_n=minimum, bin_counts=counts)
    return {"event_rate": event, "brier": brier, "ece": ece, "all_k_success": subset}


def _prepare_sources(
    observation: read_input.JSON, p: dict[str, Any]
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    if (
        observation.get("source")
        != {"format": "underwrite.evidence-bundle", "format_version": "v1"}
        or observation.get("kind") != "eval_run"
    ):
        raise read_input.ReadInputError(
            "UNSUPPORTED_PROFILE", "UNSUPPORTED_OBSERVATION_PROFILE", "/observation"
        )
    try:
        payload = observation["payload"]
        read_input.require(
            payload["schema_version"] == "underwrite.evidence-bundle.v1", "INVALID_BUNDLE_VERSION"
        )
        read_input.require(
            isinstance(payload["readings"], list) and isinstance(payload["availability"], list),
            "INVALID_BUNDLE_FIELDS",
        )
        read_input.bounded(
            len(payload["readings"]) <= read_input.MAX_CELLS
            and len(payload["availability"]) <= 2 * contract.MAX_CASES,
            "/observation",
        )
        read_input.bounded(
            sum(len(r["rows"]) for r in payload["readings"]) <= 2 * contract.MAX_CASES,
            "/observation",
        )
        try:
            semantics = contract.parse_semantics(payload.get("measurement_semantics"))
        except contract.ContractError:
            raise EventReadError(
                "INVALID_OBSERVATION", "INVALID_SEMANTICS", "/observation"
            ) from None
        sem = contract.project_semantics(semantics)
        semantic_id = contract.semantic_digest(semantics)
        if p["semantic_digest"] != semantic_id:
            raise EventReadError("INVALID_POLICY", "SEMANTIC_DIGEST_MISMATCH", "/policy")
        ids = p["expected_case_ids"]
        outcomes, outcome_population = _select(payload, sem["outcome"], ids, prediction=False)
        predictions: dict[str, Any] = {}
        prediction_population: dict[str, Any] = {
            "status": "unavailable",
            "observed_count": None,
            "missing_ids": None,
            "error_ids": None,
        }
        if sem["prediction"]["status"] != "unavailable":
            predictions, prediction_population = _select(
                payload, sem["prediction"], ids, prediction=True
            )
            prediction_population["status"] = "declared"
    except (KeyError, TypeError, AttributeError):
        raise read_input.ReadInputError(
            "INVALID_OBSERVATION", "INVALID_BUNDLE_FIELDS", "/observation"
        ) from None
    return sem, semantic_id, outcomes, outcome_population, predictions, prediction_population


def assemble_event_read(
    observation: read_input.JSON,
    policy: read_input.JSON,
    *,
    observation_digest: str,
    policy_digest: str,
) -> dict[str, Any]:
    """Measure a closed declared event profile, returning a checked read.v2."""
    p = _policy(policy)
    try:
        contract.digest(observation_digest)
        contract.digest(policy_digest)
    except contract.ContractError:
        raise EventReadError("INVALID_OBSERVATION", "INVALID_DIGEST", "/observation") from None
    sem, semantic_id, outcomes, outcome_population, predictions, prediction_population = (
        _prepare_sources(observation, p)
    )
    ids = p["expected_case_ids"]
    reasons: list[str] = []
    if "unknown" in p["assumptions"].values():
        reasons.append("assumptions_not_declared")
    if len(outcomes) != len(ids):
        reasons.append("outcome_population_incomplete")
    ep = p["eprocess"]
    direction = sem["desirable_direction"]
    if reasons:
        run = {
            "availability": "not_measured",
            "e_upper": None,
            "e_lower": None,
            "consumed_count": 0,
            "successes": 0,
            "failures": 0,
            "unused_observed_count": len(outcomes),
            "stop_reason": "withheld",
        }
        status = "withheld"
    else:
        try:
            run = run_directional(
                list(outcomes.values()),
                p0=ep["p0"],
                max_observations=ep["max_observations"],
                desirable_direction=direction,
            )
        except eprocess.EProcessError:
            raise EventReadError("INVALID_POLICY", "E_VALUE_OVERFLOW", "/policy") from None
        status = crossing_status(run["e_upper"], run["e_lower"], direction)
        reason = {
            "withheld": "conflicting_directional_crossing",
            "favorable": "favorable_threshold_crossed",
            "unfavorable": "unfavorable_threshold_crossed",
            "inconclusive": "observation_budget_exhausted"
            if run["stop_reason"] == "budget_observations"
            else "threshold_not_reached",
        }[status]
        reasons = [reason]
    directional = {
        "method": "beta-mixture-two-direction.v1",
        **ep,
        "side_alpha": contract.SIDE_ALPHA,
        "threshold": contract.THRESHOLD,
        "desirable_direction": direction,
        "assumptions": dict(p["assumptions"]),
        "error_control": {
            "method": "bonferroni_equal_two_sides",
            "upper_null": "conditional_mean_lte_p0",
            "lower_null": "conditional_mean_gte_p0",
            "per_side_alpha": contract.SIDE_ALPHA,
            "point_null_union_alpha": contract.ALPHA_TOTAL,
        },
        **run,
    }
    result: dict[str, Any] = {
        "schema": "read.v2",
        "profile": contract.PROFILE,
        "scope": "aggregate",
        "subject": {"subject_id": p["subject"]["subject_id"], "conditions": {}},
        "semantics": sem,
        "semantic_digest": semantic_id,
        "sources": {"observations": [observation_digest], "policy": policy_digest},
        "population": {
            "expected": {
                "count": len(ids),
                "ordered_inventory_digest": tagged_digest(
                    "underwrite.expected_case_inventory.v1", canonical_bytes(ids)
                ),
            },
            "outcomes": outcome_population,
            "predictions": prediction_population,
        },
        "directional": directional,
        "diagnostics": _diagnostics(outcomes, predictions, sem, p),
        "decision": {"status": status},
        "verdict": contract.STATUS_VERDICTS[status],
        "reason_codes": reasons,
        "limitations": list(contract.LIMITATIONS)
        + ([] if sem["prediction"]["status"] == "unavailable" else [contract.FORECAST_LIMITATION]),
    }
    try:
        validate_event_read(result)
    except contract.ContractError:
        raise EventReadError(
            "INVALID_OBSERVATION", "INVALID_READ_BINDING", "/observation"
        ) from None
    return result
