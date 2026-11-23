"""Pure invariants that JSON Schema cannot express across result items."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

RESULT_FORMAT_MISMATCH = "RESULT_FORMAT_MISMATCH"
RESULT_NOT_ANALYZED = "RESULT_NOT_ANALYZED"
POPULATION_UNIT_MISMATCH = "POPULATION_UNIT_MISMATCH"
MARGINAL_INELIGIBILITY_REASONS = (
    RESULT_FORMAT_MISMATCH,
    RESULT_NOT_ANALYZED,
    POPULATION_UNIT_MISMATCH,
)


class ResultInvariantError(ValueError):
    """Finite diagnostic for an internally inconsistent common result."""

    def __init__(self, reason: str, location: str) -> None:
        self.reason, self.location = reason, location
        super().__init__(reason)


def _coordinate(value: object, location: str) -> tuple[str, int, int | None]:
    if not isinstance(value, Mapping):
        raise ResultInvariantError("RESULT_COORDINATE_INVALID", location)
    value = cast(Mapping[str, object], value)
    kind = value.get("kind")
    if kind in ("case", "row", "run") and set(value) == {"kind", "index"}:
        index = value["index"]
        if type(index) is int and index >= 0:
            return cast(str, kind), index, None
    if kind == "metric_occurrence" and set(value) == {
        "kind", "case_index", "metric_index"
    }:
        case_index, metric_index = value["case_index"], value["metric_index"]
        if (
            type(case_index) is int
            and case_index >= 0
            and type(metric_index) is int
            and metric_index >= 0
        ):
            return "metric_occurrence", case_index, metric_index
    raise ResultInvariantError("RESULT_COORDINATE_INVALID", location)


def check_result_invariants(result: Mapping[str, object]) -> None:
    """Check analyzed item count and unique structural coordinates.

    This does not replace schema validation or require contiguous coordinates.
    """
    processing = result.get("processing")
    if not isinstance(processing, Mapping):
        raise ResultInvariantError("RESULT_STRUCTURE_INVALID", "/processing")
    processing = cast(Mapping[str, object], processing)
    if processing.get("state") != "analyzed":
        return
    population = result.get("population")
    if not isinstance(population, Mapping):
        raise ResultInvariantError("RESULT_STRUCTURE_INVALID", "/population")
    population = cast(Mapping[str, object], population)
    count = population.get("observed_count")
    if type(count) is not int or count < 0:
        raise ResultInvariantError("RESULT_STRUCTURE_INVALID", "/population/observed_count")
    items = result.get("items")
    if not isinstance(items, list):
        raise ResultInvariantError("RESULT_STRUCTURE_INVALID", "/items")
    items = cast(list[object], items)
    if len(items) != count:
        raise ResultInvariantError("RESULT_ITEM_COUNT_MISMATCH", "/items")
    seen: set[tuple[str, int, int | None]] = set()
    for index, item in enumerate(items):
        location = f"/items/{index}/coordinate"
        if not isinstance(item, Mapping):
            raise ResultInvariantError("RESULT_STRUCTURE_INVALID", f"/items/{index}")
        item = cast(Mapping[str, object], item)
        coordinate = _coordinate(item.get("coordinate"), location)
        if coordinate in seen:
            raise ResultInvariantError("RESULT_COORDINATE_DUPLICATE", location)
        seen.add(coordinate)


def availability_counts(result: Mapping[str, object]) -> dict[str, int] | None:
    """Count analyzed item states after schema and cross-item validation.

    Non-analyzed results declare no analyzed population, even when their item list
    is empty. The caller must validate the installed schema first.
    """
    processing = cast(Mapping[str, object], result["processing"])
    if processing["state"] != "analyzed":
        return None
    items = cast(list[Mapping[str, object]], result["items"])
    counts = {"observed": 0, "missing": 0, "error": 0, "abstained": 0}
    for item in items:
        counts[cast(str, item["availability"])] += 1
    return {**counts, "total": len(items)}


def item_histograms(
    result: Mapping[str, object],
    action_names: Sequence[str],
    binding_names: Sequence[str],
) -> tuple[dict[str, int] | None, dict[str, int] | None]:
    """Count declared item actions and semantic bindings after caller validation.

    Vocabulary comes from the installed evidence_result contract, not this pure
    calculation. Null means no analyzed population; zero-filled maps mean one.
    """
    processing = cast(Mapping[str, object], result["processing"])
    if processing["state"] != "analyzed":
        return None, None
    actions = dict.fromkeys(action_names, 0)
    bindings = dict.fromkeys(binding_names, 0)
    for item in cast(list[Mapping[str, object]], result["items"]):
        actions[cast(str, item["next_action"])] += 1
        metric = cast(Mapping[str, object], item["metric"])
        bindings[cast(str, metric["semantic_binding"])] += 1
    return actions, bindings


def _marginals_not_computed(reason: str) -> dict[str, object]:
    return {
        "state": "not_computed",
        "reason": reason,
        "population_unit": None,
        "observed_population_count_delta": None,
        "planned_population_count_delta": None,
        "availability_counts": None,
        "item_next_action_counts": None,
        "declared_semantic_binding_counts": None,
    }


def _histogram_delta(
    baseline: object, candidate: object, field: str
) -> dict[str, dict[str, int]]:
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        raise ValueError(f"{field} must contain two histograms")
    baseline = cast(Mapping[object, object], baseline)
    candidate = cast(Mapping[object, object], candidate)
    if set(baseline) != set(candidate) or not all(
        isinstance(key, str) for key in baseline
    ):
        raise ValueError(f"{field} histogram keys must match")
    if not all(type(value) is int for value in (*baseline.values(), *candidate.values())):
        raise ValueError(f"{field} histogram values must be integers")
    baseline_counts = cast(Mapping[str, int], baseline)
    candidate_counts = cast(Mapping[str, int], candidate)
    return {
        "baseline": dict(baseline_counts),
        "candidate": dict(candidate_counts),
        "delta": {
            key: candidate_counts[key] - value for key, value in baseline_counts.items()
        },
    }


def compare_result_marginals(
    baseline: Mapping[str, object], candidate: Mapping[str, object]
) -> dict[str, object]:
    """Compare only closed marginal counts from two validated inspections.

    Deltas are candidate minus baseline. The comparison does not inspect item
    coordinates or establish correspondence, semantic equivalence, or improvement.
    """
    baseline_selected = cast(Mapping[str, object], baseline["selected_result"])
    candidate_selected = cast(Mapping[str, object], candidate["selected_result"])
    if baseline_selected["format"] != candidate_selected["format"]:
        return _marginals_not_computed(RESULT_FORMAT_MISMATCH)

    baseline_processing = cast(Mapping[str, object], baseline["declared_processing"])
    candidate_processing = cast(Mapping[str, object], candidate["declared_processing"])
    if (
        baseline_processing["state"] != "analyzed"
        or candidate_processing["state"] != "analyzed"
    ):
        return _marginals_not_computed(RESULT_NOT_ANALYZED)

    baseline_population = cast(Mapping[str, object], baseline["declared_population"])
    candidate_population = cast(Mapping[str, object], candidate["declared_population"])
    if baseline_population["unit"] != candidate_population["unit"]:
        return _marginals_not_computed(POPULATION_UNIT_MISMATCH)

    baseline_planned = cast(int | None, baseline_population["planned_count"])
    candidate_planned = cast(int | None, candidate_population["planned_count"])
    return {
        "state": "computed",
        "reason": None,
        "population_unit": baseline_population["unit"],
        "observed_population_count_delta": cast(
            int, candidate_population["observed_count"]
        )
        - cast(int, baseline_population["observed_count"]),
        "planned_population_count_delta": (
            candidate_planned - baseline_planned
            if candidate_planned is not None and baseline_planned is not None
            else None
        ),
        "availability_counts": _histogram_delta(
            baseline["availability_counts"],
            candidate["availability_counts"],
            "availability_counts",
        ),
        "item_next_action_counts": _histogram_delta(
            baseline["item_next_action_counts"],
            candidate["item_next_action_counts"],
            "item_next_action_counts",
        ),
        "declared_semantic_binding_counts": _histogram_delta(
            baseline["declared_semantic_binding_counts"],
            candidate["declared_semantic_binding_counts"],
            "declared_semantic_binding_counts",
        ),
    }
