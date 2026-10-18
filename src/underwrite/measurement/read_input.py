"""Pure current event-input guards and explicit-role slot selection."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from underwrite.measurement_contract.semantics import MAX_CASES as MAX_CASES
from underwrite.measurement_contract.semantics import MAX_IDENTITY as MAX_IDENTITY

# Any is confined to decoded external JSON; an explicit role decoder creates slots.
JSON = Mapping[str, Any]
MAX_CELLS = 128
ReadReason = Literal[
    "AMBIGUOUS_SELECTOR",
    "CONFLICTING_CASE_CONDITIONS",
    "CONFLICTING_CASE_ORDER",
    "CONTRADICTORY_AVAILABILITY",
    "INVALID_AVAILABILITY",
    "INVALID_BUNDLE_FIELDS",
    "INVALID_BUNDLE_VERSION",
    "INVALID_IDENTITY",
    "INVALID_INTEGER",
    "INVALID_NUMBER",
    "UNSUPPORTED_CASE_IDENTITY",
    "UNSUPPORTED_OBSERVATION_PROFILE",
    "WORK_LIMIT_EXCEEDED",
]
READ_INPUT_REASONS: frozenset[str] = frozenset(get_args(ReadReason))


class ReadInputError(ValueError):
    """Safe category/reason/location only; never retain input or exception text."""

    def __init__(self, code: str, reason: ReadReason, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def require(condition: bool, reason: ReadReason, location: str = "/observation") -> None:
    if not condition:
        raise ReadInputError(
            "INVALID_POLICY" if location == "/policy" else "INVALID_OBSERVATION", reason, location
        )


def bounded(condition: bool, location: str) -> None:
    if not condition:
        raise ReadInputError("INPUT_LIMIT_EXCEEDED", "WORK_LIMIT_EXCEEDED", location)


def identity(value: object, location: str) -> str:
    require(isinstance(value, str), "INVALID_IDENTITY", location)
    assert isinstance(value, str)
    require(
        bool(value)
        and len(value) <= MAX_IDENTITY
        and unicodedata.normalize("NFC", value) == value
        and all(not unicodedata.category(c).startswith("C") for c in value),
        "INVALID_IDENTITY",
        location,
    )
    return value


def number(value: object, low: float, high: float, location: str) -> None:
    require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high,
        "INVALID_NUMBER",
        location,
    )


def integer(value: object, low: int, high: int, location: str) -> None:
    require(
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high,
        "INVALID_INTEGER",
        location,
    )


@dataclass(frozen=True)
class Slot:
    outcome: int | None
    prediction: float | None
    cost: float | None
    loss: str | None


def select_slots(
    payload: JSON, selector: JSON, *, decode: Callable[[JSON, str | None], Slot]
) -> tuple[dict[str, Slot], dict[str, str], list[str]]:
    """Select identities/order/availability, with an explicit consumed-value decoder."""
    matches = [r for r in payload["readings"] if all(r.get(k) == v for k, v in selector.items())]
    require(len(matches) <= 1, "AMBIGUOUS_SELECTOR")
    rows: list[JSON] = matches[0]["rows"] if matches else []
    require(isinstance(cast("object", rows), list), "INVALID_BUNDLE_FIELDS")
    losses = [a for a in payload["availability"] if all(a.get(k) == v for k, v in selector.items())]
    states: dict[str, str] = {}
    splits: dict[str, str] = {}
    for item in losses:
        case_id = identity(item["case_id"], "/observation")
        require(
            item.get("epoch") is None and type(item["repeat"]) is int and item["repeat"] == 0,
            "UNSUPPORTED_CASE_IDENTITY",
        )
        require(
            case_id not in states and item["availability"] in ("observed", "missing", "error"),
            "INVALID_AVAILABILITY",
        )
        states[case_id] = item["availability"]
        if item.get("split") is not None:
            splits[case_id] = identity(item["split"], "/observation")
    slots: dict[str, Slot] = {}
    previous = -1
    for row in rows:
        case_id = identity(row["case_id"], "/observation")
        require(
            case_id not in slots
            and row.get("epoch") is None
            and type(row["repeat"]) is int
            and row["repeat"] == 0,
            "UNSUPPORTED_CASE_IDENTITY",
        )
        integer(row["sequence"], 0, 2**53 - 1, "/observation")
        require(row["sequence"] > previous, "CONFLICTING_CASE_ORDER")
        previous = row["sequence"]
        if row.get("split") is not None:
            require(
                case_id not in splits or splits[case_id] == row["split"],
                "CONFLICTING_CASE_CONDITIONS",
            )
            splits[case_id] = identity(row["split"], "/observation")
        slots[case_id] = decode(row, states.get(case_id))
    order = list(slots)
    for case_id, state in states.items():
        require(state != "observed" or case_id in slots, "CONTRADICTORY_AVAILABILITY")
        slots.setdefault(case_id, Slot(None, None, None, state if state != "observed" else None))
    return slots, splits, order
