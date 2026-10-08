"""Project evidence into deterministically ordered condition cells.

Per-instrument observations of the same case are combined before stratification.
Sparse cells remain present and unmeasured, and their measurement callback is
never invoked. Cell order depends on axis/value pairs rather than input order."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard

MEASURED = "measured"
NOT_MEASURED = "not_measured"
REASON_CELL_BELOW_MIN_N = "cell_below_min_n"
REASON_NO_OBSERVATIONS = "no_observations"
MEASURED_VERDICTS: tuple[str, ...] = ("pass", "warn", "fail")
REASON_CODES: tuple[str, ...] = (  # read.v1 `$defs.reason_codes`, in the contract's order
    "below_quorum",
    "required_instrument_missing",
    "required_instrument_error",
    REASON_CELL_BELOW_MIN_N,
    "bin_below_min_n",
    "n_below_k",
    "wealth_absorbed",
    REASON_NO_OBSERVATIONS,
    "signal_not_measured",
)
_MEASURE_REASONS = tuple(code for code in REASON_CODES if code != REASON_CELL_BELOW_MIN_N)
_PAIR = 2  # a cell measurement is exactly two items

CellKey = tuple[tuple[str, str], ...]


class StratifyError(ValueError):
    """Malformed axes, policy, cases, requested keys, or an ill-formed cell measurement."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class Case:
    """One case: its identity, its condition values (sorted pairs), and an opaque payload."""

    case_id: str
    conditions: tuple[tuple[str, str], ...]
    payload: object


@dataclass(frozen=True)
class Cell:
    """One stratum: the (axis, value) key in axes order and what was measured in it."""

    key: CellKey
    n: int
    availability: str
    verdict: str | None
    score: float | None
    reason_codes: tuple[str, ...]


Outcome = tuple[str, float] | tuple[str, tuple[str, ...]]
Measure = Callable[[tuple[Case, ...]], Outcome]


def _is_str(value: object) -> TypeGuard[str]:
    return type(value) is str


def _checked_pairs(conditions: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    for key, value in conditions.items():
        if not _is_str(key) or not key:
            raise StratifyError("INVALID_CONDITION_KEY", repr(key))
        if not _is_str(value):
            raise StratifyError("INVALID_CONDITION_VALUE", f"{key}={value!r}")
    return tuple(sorted(conditions.items()))


def make_case(case_id: str, conditions: Mapping[str, str], payload: object) -> Case:
    """The only validated way to a Case: non-empty id, string-to-string conditions."""
    if not _is_str(case_id) or not case_id:
        raise StratifyError("INVALID_CASE_ID", repr(case_id))
    return Case(case_id, _checked_pairs(conditions), payload)


def _checked_axes(axes: Sequence[str]) -> tuple[str, ...]:
    if isinstance(axes, str):
        raise StratifyError("INVALID_AXES", repr(axes))
    checked = tuple(axes)
    if not checked or len(set(checked)) != len(checked):
        raise StratifyError("INVALID_AXES", repr(checked))
    for axis in checked:
        if not _is_str(axis) or not axis:
            raise StratifyError("INVALID_AXES", repr(axis))
    return checked


def _checked_min_cell_n(min_cell_n: int) -> int:
    if type(min_cell_n) is not int or min_cell_n < 1:
        raise StratifyError("INVALID_MIN_CELL_N", repr(min_cell_n))
    return min_cell_n


def _cell_key(case: Case, axes: tuple[str, ...]) -> CellKey:
    values = dict(case.conditions)
    key: list[tuple[str, str]] = []
    for axis in axes:
        if axis not in values:
            raise StratifyError("MISSING_AXIS", f"{case.case_id}:{axis}")
        value = values[axis]
        if not _is_str(value):
            raise StratifyError("INVALID_CONDITION_VALUE", f"{case.case_id}:{axis}={value!r}")
        key.append((axis, value))
    return tuple(key)


def _checked_cases(cases: Iterable[object]) -> tuple[Case, ...]:
    # Read once: a second pass over a one-shot iterable would see nothing and drop the
    # whole population without an error.
    checked: list[Case] = []
    seen: set[str] = set()
    for item in cases:
        if not isinstance(item, Case):
            raise StratifyError("INVALID_CASE", repr(item))
        if item.case_id in seen:
            raise StratifyError("DUPLICATE_CASE", item.case_id)
        seen.add(item.case_id)
        checked.append(item)
    return tuple(checked)


def _checked_requested(
    requested: Sequence[Mapping[str, str]], axes: tuple[str, ...]
) -> set[CellKey]:
    keys: set[CellKey] = set()
    for mapping in requested:
        if set(mapping) != set(axes):
            raise StratifyError("INVALID_REQUESTED_KEY", repr(dict(mapping)))
        values = dict(_checked_pairs(mapping))
        keys.add(tuple((axis, values[axis]) for axis in axes))
    return keys


def _group(cases: Sequence[Case], axes: tuple[str, ...]) -> dict[CellKey, tuple[Case, ...]]:
    members: dict[CellKey, list[Case]] = {}
    for case in _checked_cases(cases):
        members.setdefault(_cell_key(case, axes), []).append(case)
    return {key: tuple(members[key]) for key in sorted(members)}


def group(cases: Sequence[Case], axes: Sequence[str]) -> dict[CellKey, tuple[Case, ...]]:
    """Cases per cell key, keys sorted, members in input order. Validates cases and axes."""
    return _group(cases, _checked_axes(axes))


def _is_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    return isinstance(value, tuple)


def _pair(result: object) -> tuple[object, object]:
    items = result if _is_tuple(result) else ()
    if len(items) != _PAIR:
        raise StratifyError("INVALID_MEASURE", repr(result))
    return items[0], items[1]


def _checked_reasons(value: object) -> tuple[str, ...]:
    items = value if _is_tuple(value) else ()
    reasons = tuple(r for r in items if _is_str(r) and r in _MEASURE_REASONS)
    if not reasons or len(reasons) != len(items) or len(set(reasons)) != len(reasons):
        raise StratifyError("INVALID_MEASURE", repr(value))
    return reasons


def _checked_score(value: object) -> float:
    # The range comparison also refuses NaN and both infinities, and it is exact for ints
    # of any size, so `float` below never overflows.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise StratifyError("INVALID_MEASURE", repr(value))
    return float(value)


def _cell(key: CellKey, members: tuple[Case, ...], min_cell_n: int, measure: Measure) -> Cell:
    n = len(members)
    if n == 0:
        return Cell(key, 0, NOT_MEASURED, None, None, (REASON_NO_OBSERVATIONS,))
    if n < min_cell_n:
        return Cell(key, n, NOT_MEASURED, None, None, (REASON_CELL_BELOW_MIN_N,))
    first, second = _pair(measure(members))
    if first == NOT_MEASURED:
        return Cell(key, n, NOT_MEASURED, None, None, _checked_reasons(second))
    if not _is_str(first) or first not in MEASURED_VERDICTS:
        raise StratifyError("INVALID_MEASURE", repr(first))
    return Cell(key, n, MEASURED, first, _checked_score(second), ())


def stratify(
    cases: Sequence[Case],
    axes: Sequence[str],
    min_cell_n: int,
    measure: Measure,
    *,
    requested: Sequence[Mapping[str, str]] = (),
) -> tuple[Cell, ...]:
    """Cells for every key seen in `cases` or named in `requested`, in key order.

    `measure` is called once per cell that reaches `min_cell_n`, with that cell's members in
    input order, and never for a sparse or empty cell. A requested key with no cases is
    reported with n = 0 and `no_observations`. The caller's sequences are read, not changed.
    """
    checked_axes = _checked_axes(axes)
    floor = _checked_min_cell_n(min_cell_n)
    grouped = _group(cases, checked_axes)
    for key in _checked_requested(requested, checked_axes):
        grouped.setdefault(key, ())
    return tuple(_cell(key, grouped[key], floor, measure) for key in sorted(grouped))


def project(cell: Cell) -> dict[str, object]:
    """The read.v1 `$defs.stratum` shape of one cell."""
    return {
        "key": dict(cell.key),
        "n": cell.n,
        "availability": cell.availability,
        "verdict": cell.verdict,
        "score": cell.score,
        "reason_codes": list(cell.reason_codes),
    }
