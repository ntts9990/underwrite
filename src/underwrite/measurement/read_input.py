"""Bounded fixed-profile boundary for already decoded observation and policy JSON."""

from __future__ import annotations

import heapq
import re
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from underwrite.measurement import availability, eprocess, stratify

# Any is confined to decoded external JSON; prepare replaces source rows with DTOs.
JSON = Mapping[str, Any]
MAX_CASES = 2000
MAX_CELLS = 128
MAX_IDENTITY = 128
MAX_WORK = 2 * MAX_CASES**2
# statistical_seed.v1's const; the pure layer cannot read contracts/, so
# tests/gates/test_s5_statistical_contract.py pins this copy to its owner.
CANONICAL_SEED = 42
ReadReason = Literal[
    "AMBIGUOUS_SELECTOR",
    "CONFLICTING_CASE_CONDITIONS",
    "CONFLICTING_CASE_ORDER",
    "CONTRADICTORY_AVAILABILITY",
    "INVALID_AVAILABILITY",
    "INVALID_BUNDLE_FIELDS",
    "INVALID_BUNDLE_VERSION",
    "INVALID_CALIBRATION_MAPPING",
    "INVALID_FUSION",
    "INVALID_IDENTITY",
    "INVALID_INTEGER",
    "INVALID_NUMBER",
    "INVALID_PANEL_BANDS",
    "INVALID_POLICY_FIELDS",
    "INVALID_REQUESTED_CELL",
    "INVALID_REQUIRED_INSTRUMENTS",
    "INVALID_SELECTOR",
    "INVALID_STRATIFICATION",
    "INVALID_SUBJECT",
    "MISSING_SPLIT",
    "NONCANONICAL_EPROCESS",
    "SELECTOR_NOT_REQUIRED",
    "SUBJECT_CONDITION_CONFLICT",
    "UNSUPPORTED_CASE_IDENTITY",
    "UNSUPPORTED_OBSERVATION_PROFILE",
    "UNSUPPORTED_POLICY_PROFILE",
    "UNSUPPORTED_REQUIRED_EVIDENCE",
    "WORK_LIMIT_EXCEEDED",
    "COST_REQUIRED",
    "COST_OVERFLOW",
    "E_VALUE_OVERFLOW",
    "INVALID_DIGEST",
    "INVALID_EPROCESS",
    "NO_TRIALS",
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


def _policy(p: JSON) -> None:
    location = "/policy"
    if (
        p.get("schema") != "measurement_policy.v2"
        or p.get("profile") != "binary-calibration-two-stage.v1"
    ):
        raise ReadInputError("UNSUPPORTED_PROFILE", "UNSUPPORTED_POLICY_PROFILE", location)
    try:
        for name in ("min_cell_n", "min_bin_n"):
            integer(p[name], 1, 2**53 - 1, location)
        integer(p["seed"], CANONICAL_SEED, CANONICAL_SEED, location)
        required = p["required_instruments"]
        require(isinstance(required, list), "INVALID_REQUIRED_INSTRUMENTS", location)
        bounded(len(required) <= MAX_CELLS, location)
        for name in required:
            identity(name, location)
        availability.make_policy(required, p["quorum"])
        _selectors(p["inputs"], required)
        ep = p["eprocess"]
        eprocess.check_policy(eprocess.EProcessPolicy(**ep))
        require(
            (ep["p0"], ep["prior_a"], ep["prior_b"], ep["pass_e"])
            == (
                eprocess.CONTRACT_P0,
                eprocess.CONTRACT_PRIOR_A,
                eprocess.CONTRACT_PRIOR_B,
                eprocess.CONTRACT_PASS_E,
            ),
            "NONCANONICAL_EPROCESS",
            location,
        )
        identity(ep["null_id"], location)
        require(
            p["fusion"] == {"combine": "mean", "independence": None}, "INVALID_FUSION", location
        )
        cal, panel, strata = p["calibration"], p["panel"], p["stratification"]
        for name, limit in (("bins", 128), ("k", MAX_CASES), ("anchor_min_samples", 2**53 - 1)):
            integer(cal[name], 1, limit, location)
        for name in ("pass_ece", "warn_ece"):
            number(cal[name], 0, 1, location)
        require(
            cal["pass_ece"] <= cal["warn_ece"]
            and (cal["prediction_event"], cal["prediction_field"], cal["label_field"])
            == ("case passes", "score", "outcome"),
            "INVALID_CALIBRATION_MAPPING",
            location,
        )
        for name in ("pass_at", "warn_at", "escalate_at", "conflict_confidence"):
            number(panel[name], 0, 1, location)
        require(panel["warn_at"] <= panel["pass_at"], "INVALID_PANEL_BANDS", location)
        _subject(p["subject"])
        require(
            strata["mode"] in ("none", "split")
            and strata["axes"] == ([] if strata["mode"] == "none" else ["split"]),
            "INVALID_STRATIFICATION",
            location,
        )
        require(isinstance(strata["requested"], list), "INVALID_STRATIFICATION", location)
        bounded(len(strata["requested"]) <= MAX_CELLS, location)
        require(
            strata["mode"] != "none" or not strata["requested"], "INVALID_STRATIFICATION", location
        )
        for cell in strata["requested"]:
            require(set(cell) == {"split"}, "INVALID_REQUESTED_CELL", location)
            identity(cell["split"], location)
    except (
        KeyError,
        TypeError,
        AttributeError,
        OverflowError,
        availability.AvailabilityInputError,
        eprocess.EProcessError,
    ):
        raise ReadInputError("INVALID_POLICY", "INVALID_POLICY_FIELDS", location) from None


def _subject(subject: JSON) -> None:
    location = "/policy"
    require(
        re.fullmatch(r"[A-Za-z0-9_.:-]+", identity(subject["subject_id"], location)) is not None,
        "INVALID_SUBJECT",
        location,
    )
    require(
        set(subject) <= {"subject_id", "conditions", "label"}
        and set(subject["conditions"]) <= {"split"},
        "INVALID_SUBJECT",
        location,
    )
    for value in subject["conditions"].values():
        identity(value, location)
    if "label" in subject:
        require(isinstance(subject["label"], str), "INVALID_SUBJECT", location)


def _selectors(inputs: JSON, required: list[str]) -> None:
    for slot in ("eprocess", "calibration"):
        selector = inputs[slot]
        require(set(selector) == {"instrument", "metric_id"}, "INVALID_SELECTOR", "/policy")
        for value in selector.values():
            identity(value, "/policy")
        require(selector["instrument"] in required, "SELECTOR_NOT_REQUIRED", "/policy")


@dataclass(frozen=True)
class Slot:
    outcome: int | None
    prediction: float | None
    cost: float | None
    loss: str | None


@dataclass(frozen=True)
class Prepared:
    policy: JSON
    cases: tuple[stratify.Case, ...]
    instruments: tuple[str, str]
    extra_losses: Mapping[str, str]


def _slot(row: JSON, state: str | None) -> Slot:
    outcome, prediction, cost = (
        row["outcome"],
        row["score"],
        row.get("payload", {}).get("cost"),
    )
    if outcome is not None:
        integer(outcome, 0, 1, "/observation")
    if prediction is not None:
        number(prediction, 0, 1, "/observation")
    if cost is not None:
        number(cost, 0, sys.float_info.max, "/observation")
    require(
        state not in ("error", "missing") or (outcome is None and prediction is None),
        "CONTRADICTORY_AVAILABILITY",
    )
    return Slot(outcome, prediction, cost, state if state in ("error", "missing") else None)


def _selected(payload: JSON, selector: JSON) -> tuple[dict[str, Slot], dict[str, str], list[str]]:
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
        slots[case_id] = _slot(row, states.get(case_id))
    order = list(slots)
    for case_id, state in states.items():
        require(state != "observed" or case_id in slots, "CONTRADICTORY_AVAILABILITY")
        slots.setdefault(case_id, Slot(None, None, None, state if state != "observed" else None))
    return slots, splits, order


def _ordered(ids: list[str], orders: list[list[str]]) -> list[str]:
    rank = {case_id: i for i, case_id in enumerate(ids)}
    edges: dict[str, set[str]] = {case_id: set() for case_id in ids}
    incoming = dict.fromkeys(ids, 0)
    for order in orders:
        for left, right in zip(order, order[1:], strict=False):
            if right not in edges[left]:
                edges[left].add(right)
                incoming[right] += 1
    ready = [rank[key] for key in ids if not incoming[key]]
    heapq.heapify(ready)
    result: list[str] = []
    while ready:
        key = ids[heapq.heappop(ready)]
        result.append(key)
        for child in edges[key]:
            incoming[child] -= 1
            if not incoming[child]:
                heapq.heappush(ready, rank[child])
    require(len(result) == len(ids), "CONFLICTING_CASE_ORDER")
    return result


def prepare(observation: JSON, policy: JSON) -> Prepared:
    """Validate consumed semantics and total bounded work before any statistics run."""
    _policy(policy)
    if (
        observation.get("source")
        != {"format": "underwrite.evidence-bundle", "format_version": "v1"}
        or observation.get("kind") != "eval_run"
    ):
        raise ReadInputError(
            "UNSUPPORTED_PROFILE", "UNSUPPORTED_OBSERVATION_PROFILE", "/observation"
        )
    try:
        payload = observation["payload"]
        require(
            payload["schema_version"] == "underwrite.evidence-bundle.v1",
            "INVALID_BUNDLE_VERSION",
        )
        require(
            isinstance(payload["readings"], list) and isinstance(payload["availability"], list),
            "INVALID_BUNDLE_FIELDS",
        )
        bounded(
            len(payload["readings"]) <= MAX_CELLS and len(payload["availability"]) <= 2 * MAX_CASES,
            "/observation",
        )
        bounded(sum(len(r["rows"]) for r in payload["readings"]) <= 2 * MAX_CASES, "/observation")
        selected = [
            _selected(payload, policy["inputs"][slot]) for slot in ("eprocess", "calibration")
        ]
        ids = list(dict.fromkeys(key for slots, _, _ in selected for key in slots))
        bounded(len(ids) <= MAX_CASES, "/observation")
        instruments = tuple(policy["inputs"][s]["instrument"] for s in ("eprocess", "calibration"))
        extra: dict[str, str] = {}
        for name in set(policy["required_instruments"]) - set(instruments):
            states = [a["availability"] for a in payload["availability"] if a["instrument"] == name]
            require(
                all(s in ("error", "missing") for s in states)
                and not any(r["rows"] for r in payload["readings"] if r["instrument"] == name),
                "UNSUPPORTED_REQUIRED_EVIDENCE",
            )
            extra[name] = availability.classify_loss(states)
        cases: list[stratify.Case] = []
        for key in _ordered(ids, [order for _, _, order in selected]):
            splits = {splits[key] for _, splits, _ in selected if key in splits}
            require(len(splits) <= 1, "CONFLICTING_CASE_CONDITIONS")
            conditions = {"split": next(iter(splits))} if splits else {}
            require(
                all(conditions.get(k) == v for k, v in policy["subject"]["conditions"].items()),
                "SUBJECT_CONDITION_CONFLICT",
            )
            require(policy["stratification"]["mode"] != "split" or bool(splits), "MISSING_SPLIT")
            cases.append(
                stratify.make_case(
                    key, conditions, tuple(slots.get(key) for slots, _, _ in selected)
                )
            )
        cells = {tuple(case.conditions) for case in cases} | {
            tuple(sorted(c.items())) for c in policy["stratification"]["requested"]
        }
        bounded(
            policy["stratification"]["mode"] == "none" or len(cells) <= MAX_CELLS, "/observation"
        )
        bounded(2 * len(cases) ** 2 <= MAX_WORK, "/observation")
        return Prepared(policy, tuple(cases), (instruments[0], instruments[1]), extra)
    except (KeyError, TypeError, AttributeError):
        raise ReadInputError(
            "INVALID_OBSERVATION", "INVALID_BUNDLE_FIELDS", "/observation"
        ) from None
