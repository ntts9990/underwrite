"""Card: measurement-semantics | Paper: ADR-020 | Stage: semantic-repair.

Validate supplied calculations' contracts, without authenticating their arithmetic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, cast

from underwrite_core.canonical import canonical_bytes, content_digest

from .semantics import (
    ALPHA_TOTAL,
    ASSUMPTIONS,
    FORECAST_LIMITATION,
    LIMITATIONS,
    MAX_CASES,
    PROFILE,
    SIDE_ALPHA,
    STATUS_VERDICTS,
    THRESHOLD,
    closed,
    digest,
    integer,
    number,
    parse_semantics,
    project_semantics,
    require,
    semantic_digest,
    text,
)


@dataclass(frozen=True)
class ReadBinding:
    subject_id: str
    policy_digest: str
    semantic_digest: str
    read_digest: str
    verdict: Literal["pass", "fail", "warn", "not_measured"]
    availability: Literal["full", "partial", "below_quorum"]
    schema: str
    profile: str
    reason_codes: tuple[str, ...]


def crossing_status(upper: float, lower: float, direction: str) -> str:
    require(direction in ("higher", "lower"))
    up, down = upper >= THRESHOLD, lower >= THRESHOLD
    if up and down:
        return "withheld"
    if not up and not down:
        return "inconclusive"
    return "favorable" if up == (direction == "higher") else "unfavorable"


def _ids(value: object) -> list[str]:
    require(isinstance(value, list))
    result = [text(item) for item in cast(list[object], value)]
    require(len(result) <= MAX_CASES and len(set(result)) == len(result))
    return result


def _population(value: object, count: int) -> tuple[int, list[str], list[str]]:
    row = closed(value, "observed_count missing_ids error_ids")
    observed = integer(row["observed_count"])
    missing, errors = _ids(row["missing_ids"]), _ids(row["error_ids"])
    require(not set(missing).intersection(errors))
    require(observed + len(missing) + len(errors) == count, "INCONSISTENT_READ")
    return observed, missing, errors


def _diagnostic(value: object, fields: str, method: str, unit: str) -> dict[str, Any]:
    row = closed(value, "method unit availability value n reason_codes " + fields)
    require(row["method"] == method and row["unit"] == unit)
    integer(row["n"])
    require(row["availability"] in ("measured", "not_measured"))
    require(isinstance(row["reason_codes"], list))
    if row["availability"] == "measured":
        number(row["value"], 0, 1)
        require(row["reason_codes"] == [])
    else:
        require(row["value"] is None and bool(row["reason_codes"]))
    return row


def _diagnostics(
    value: object,
    semantics: dict[str, Any],
    observed: int,
    expected: int,
    predictions: dict[str, Any],
    outcome_missing: set[str],
) -> None:
    obj = closed(value, "event_rate brier ece all_k_success")
    event = _diagnostic(
        obj["event_rate"], "scope positive_count", "observed-event-fraction.v1", "proportion"
    )
    require(event["scope"] == "observed_expected_cases" and event["n"] == observed)
    positive = integer(event["positive_count"], 0, observed)
    require(event["reason_codes"] == ([] if observed else ["no_observed_outcomes"]))
    if observed:
        require(
            event["availability"] == "measured"
            and math.isclose(event["value"], positive / observed, rel_tol=1e-12, abs_tol=1e-15)
        )
    all_k = _diagnostic(
        obj["all_k_success"],
        "estimand c k",
        "uniform-subset-all-success.v1",
        "fraction_of_k_subsets",
    )
    require(
        all_k["estimand"] == "all_k_satisfied" and all_k["n"] == observed and all_k["c"] == positive
    )
    integer(all_k["c"])
    k = integer(all_k["k"], 1)
    reasons: list[str] = (
        ["outcome_population_incomplete"]
        if observed != expected
        else (["n_below_k"] if observed < k else [])
    )
    require(
        all_k["reason_codes"] == reasons
        and all_k["availability"] == ("not_measured" if reasons else "measured")
    )
    brier = _diagnostic(obj["brier"], "", "brier-mean-squared-error.v1", "squared_probability")
    ece = _diagnostic(
        obj["ece"], "bins min_bin_n bin_counts", "fixed-width-absolute-gap.v1", "probability"
    )
    bins = integer(ece["bins"], 1, 128)
    minimum = integer(ece["min_bin_n"], 1, 2**53 - 1)
    require(isinstance(ece["bin_counts"], list))
    counts = cast(list[object], ece["bin_counts"])
    require(len(counts) == bins)
    bin_counts = [integer(n) for n in counts]
    require(sum(bin_counts) == ece["n"] == brier["n"])
    if predictions["status"] == "unavailable":
        require(brier["n"] == 0)
        reasons = ["prediction_semantics_unavailable"]
    else:
        missing = outcome_missing | set(predictions["missing_ids"]) | set(predictions["error_ids"])
        require(brier["n"] == expected - len(missing))
        reasons = []
        if semantics["prediction"]["timing"] == "unknown":
            reasons.append("forecast_timing_unknown")
        if brier["n"] != expected:
            reasons.append("paired_population_incomplete")
    require(brier["reason_codes"] == reasons)
    require(brier["availability"] == ("not_measured" if reasons else "measured"))
    ece_reasons = reasons or (
        ["bin_below_min_n"] if any(0 < n < minimum for n in bin_counts) else []
    )
    require(ece["reason_codes"] == ece_reasons)
    require(ece["availability"] == ("not_measured" if ece_reasons else "measured"))


def _directional(
    obj: dict[str, Any], semantics: dict[str, Any], observed: int, total: int
) -> list[str]:
    directional = closed(
        obj["directional"],
        "method availability p0 alpha_total side_alpha threshold prior_a prior_b "
        "max_observations desirable_direction error_control assumptions e_upper e_lower "
        "consumed_count successes failures unused_observed_count stop_reason",
    )
    require(directional["method"] == "beta-mixture-two-direction.v1")
    require(0 < number(directional["p0"], 0, 1) < 1)
    for key, expected_value in (
        ("alpha_total", ALPHA_TOTAL),
        ("side_alpha", SIDE_ALPHA),
        ("threshold", THRESHOLD),
        ("prior_a", 1),
        ("prior_b", 1),
    ):
        require(number(directional[key]) == expected_value)
    budget = directional["max_observations"]
    if budget is not None:
        integer(budget, 1, 2**53 - 1)
    require(directional["desirable_direction"] == semantics["desirable_direction"])
    require(
        directional["error_control"]
        == {
            "method": "bonferroni_equal_two_sides",
            "upper_null": "conditional_mean_lte_p0",
            "lower_null": "conditional_mean_gte_p0",
            "per_side_alpha": SIDE_ALPHA,
            "point_null_union_alpha": ALPHA_TOTAL,
        }
    )
    assumptions = closed(directional["assumptions"], " ".join(ASSUMPTIONS))
    require(all(status in ("declared", "unknown") for status in assumptions.values()))
    count = integer(directional["consumed_count"], 0, observed)
    successes = integer(directional["successes"], 0, count)
    failures = integer(directional["failures"], 0, count)
    require(
        count == successes + failures
        and integer(directional["unused_observed_count"]) == observed - count
    )
    decision = closed(obj["decision"], "status")
    reasons: list[str] = []
    if "unknown" in assumptions.values():
        reasons.append("assumptions_not_declared")
    if observed != total:
        reasons.append("outcome_population_incomplete")
    if reasons:
        require(
            directional["availability"] == "not_measured"
            and directional["e_upper"] is None
            and directional["e_lower"] is None
            and count == 0
            and directional["stop_reason"] == "withheld"
        )
        status = "withheld"
    else:
        require(directional["availability"] == "measured" and count > 0)
        upper, lower = number(directional["e_upper"]), number(directional["e_lower"])
        status = crossing_status(upper, lower, semantics["desirable_direction"])
        require(budget is None or count <= budget)
        stop = directional["stop_reason"]
        if status == "withheld":
            require(stop == "conflicting_crossing")
            reasons = ["conflicting_directional_crossing"]
        elif status in ("favorable", "unfavorable"):
            require(stop == ("upper_crossing" if upper >= THRESHOLD else "lower_crossing"))
            reasons = [status + "_threshold_crossed"]
        elif budget is not None and count == budget:
            require(stop == "budget_observations")
            reasons = ["observation_budget_exhausted"]
        else:
            require(count == observed and stop == "end_of_population")
            reasons = ["threshold_not_reached"]
    require(
        decision["status"] == status
        and obj["verdict"] == STATUS_VERDICTS[status]
        and obj["reason_codes"] == reasons,
        "INCONSISTENT_READ",
    )
    return reasons


def validate_event_read(value: object) -> ReadBinding:
    obj = closed(
        value,
        "schema profile scope subject semantics semantic_digest sources population "
        "directional diagnostics decision verdict reason_codes limitations",
    )
    require(
        obj["schema"] == "read.v2" and obj["profile"] == PROFILE and obj["scope"] == "aggregate"
    )
    subject = closed(obj["subject"], "subject_id conditions")
    subject_id = text(subject["subject_id"])
    require(subject["conditions"] == {})
    semantics_value = parse_semantics(obj["semantics"])
    semantics = project_semantics(semantics_value)
    semantic_id = semantic_digest(semantics_value)
    require(obj["semantic_digest"] == semantic_id, "SEMANTIC_DIGEST_MISMATCH")
    sources = closed(obj["sources"], "observations policy")
    policy_id = digest(sources["policy"])
    require(isinstance(sources["observations"], list))
    observations = cast(list[object], sources["observations"])
    require(len(observations) == 1)
    digest(observations[0])
    population = closed(obj["population"], "expected outcomes predictions")
    expected = closed(population["expected"], "count ordered_inventory_digest")
    total = integer(expected["count"], 1)
    digest(expected["ordered_inventory_digest"])
    observed, missing, errors = _population(population["outcomes"], total)
    prediction = closed(population["predictions"], "status observed_count missing_ids error_ids")
    unavailable = semantics["prediction"]["status"] == "unavailable"
    if unavailable:
        require(
            prediction
            == {
                "status": "unavailable",
                "observed_count": None,
                "missing_ids": None,
                "error_ids": None,
            }
        )
    else:
        require(prediction["status"] == "declared")
        _population(
            {key: prediction[key] for key in ("observed_count", "missing_ids", "error_ids")}, total
        )
    reasons = _directional(obj, semantics, observed, total)
    _diagnostics(obj["diagnostics"], semantics, observed, total, prediction, set(missing + errors))
    positive = obj["diagnostics"]["event_rate"]["positive_count"]
    require(
        obj["directional"]["successes"] <= positive
        and obj["directional"]["failures"] <= observed - positive,
        "INCONSISTENT_READ",
    )
    require(
        obj["limitations"] == list(LIMITATIONS) + ([] if unavailable else [FORECAST_LIMITATION])
    )
    canonical_bytes(obj)
    return ReadBinding(
        subject_id,
        policy_id,
        semantic_id,
        content_digest(obj),
        obj["verdict"],
        "full" if observed == total else ("below_quorum" if observed == 0 else "partial"),
        "read.v2",
        PROFILE,
        tuple(reasons),
    )
