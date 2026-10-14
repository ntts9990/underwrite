"""Pure invariants that JSON Schema cannot express across result items."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast


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
