"""Compute Brier score, calibration bins and pass-to-the-k estimates.

Callers supply bin count and minimum cell population explicitly. Brier uses every
valid pair. ECE requires sufficient populated bins; sparse views stay unmeasured.
Fixed-width bins include 1.0 in the final bin. Empty bins do not invent samples."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard

MEASURED = "measured"
NOT_MEASURED = "not_measured"
REASON_BIN_BELOW_MIN_N = "bin_below_min_n"
REASON_N_BELOW_K = "n_below_k"


class CalibrationError(ValueError):
    """Invalid input to a calibration measure. ``args[0]`` is the code."""


@dataclass(frozen=True, slots=True)
class Pair:
    """One scored prediction: a probability in [0, 1] and its 0/1 label."""

    prediction: float
    label: int


@dataclass(frozen=True, slots=True)
class BrierMeasure:
    availability: str
    value: float | None
    n: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ECEMeasure:
    availability: str
    value: float | None
    n: int
    reason_codes: tuple[str, ...]
    bins: int
    min_bin_n: int
    bin_counts: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PassKMeasure:
    availability: str
    value: float | None
    n: int
    reason_codes: tuple[str, ...]
    k: int
    successes: int


def _is_real(value: object) -> TypeGuard[float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int_at_least(value: object, floor: int, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < floor:
        raise CalibrationError(code)
    return value


def _positive_int(value: object, code: str) -> int:
    return _int_at_least(value, 1, code)


def make_pair(prediction: object, label: object) -> Pair:
    """Validate one (prediction, label) pair; the only way to a trusted ``Pair``."""
    if not _is_real(prediction) or not math.isfinite(prediction) or not 0.0 <= prediction <= 1.0:
        raise CalibrationError("INVALID_PREDICTION")
    if not _is_real(label) or label not in (0, 1):
        raise CalibrationError("INVALID_LABEL")
    return Pair(float(prediction), int(label))


def _validated(pairs: Sequence[Pair]) -> tuple[Pair, ...]:
    """Re-check every pair (a ``Pair`` built without ``make_pair`` carries no guarantee)."""
    checked = tuple(make_pair(pair.prediction, pair.label) for pair in pairs)
    if not checked:
        raise CalibrationError("NO_PAIRS")
    return checked


def brier(pairs: Sequence[Pair]) -> BrierMeasure:
    """Mean squared error of the predictions; always measured on valid, non-empty input."""
    checked = _validated(pairs)
    total = math.fsum((pair.prediction - pair.label) ** 2 for pair in checked)
    return BrierMeasure(MEASURED, total / len(checked), len(checked), ())


def bin_index(prediction: float, bins: int) -> int:
    """Fixed-width bin of ``prediction``; the top edge (1.0) folds into the last bin."""
    return min(int(prediction * bins), bins - 1)


def _binned(pairs: Sequence[Pair], bins: int) -> tuple[list[int], list[float], list[float]]:
    """One pass over the pairs: per-bin count, prediction sum and label sum."""
    counts = [0] * bins
    prediction_sums = [0.0] * bins
    label_sums = [0.0] * bins
    for pair in pairs:
        index = bin_index(pair.prediction, bins)
        counts[index] += 1
        prediction_sums[index] += pair.prediction
        label_sums[index] += pair.label
    return counts, prediction_sums, label_sums


def _gap_weighted(
    counts: Sequence[int], prediction_sums: Sequence[float], label_sums: Sequence[float]
) -> float:
    """Sum over populated bins of (count / n) * |event rate - mean prediction|."""
    total = sum(counts)
    return math.fsum(
        (count / total) * abs(label_sum / count - prediction_sum / count)
        for count, prediction_sum, label_sum in zip(
            counts, prediction_sums, label_sums, strict=True
        )
        if count
    )


def ece(pairs: Sequence[Pair], bins: int, min_bin_n: int) -> ECEMeasure:
    """Expected calibration error over fixed-width bins, measured only when every populated
    bin has at least ``min_bin_n`` points; otherwise not_measured with ``bin_below_min_n``."""
    checked = _validated(pairs)
    bins = _positive_int(bins, "INVALID_BINS")
    min_bin_n = _positive_int(min_bin_n, "INVALID_MIN_BIN_N")
    counts, prediction_sums, label_sums = _binned(checked, bins)
    recorded = tuple(counts)
    if any(0 < count < min_bin_n for count in counts):
        return ECEMeasure(
            NOT_MEASURED, None, len(checked), (REASON_BIN_BELOW_MIN_N,), bins, min_bin_n, recorded
        )
    value = _gap_weighted(counts, prediction_sums, label_sums)
    return ECEMeasure(MEASURED, value, len(checked), (), bins, min_bin_n, recorded)


def pass_k(successes: int, trials: int, k: int) -> PassKMeasure:
    """P(all k of k independent draws without replacement succeed) = C(c, k) / C(n, k).

    With ``trials < k`` the estimator is undefined and the view is not_measured
    (``n_below_k``); that is arithmetic, not a policy threshold.
    """
    trials = _int_at_least(trials, 0, "INVALID_TRIALS")
    successes = _int_at_least(successes, 0, "INVALID_SUCCESSES")
    if successes > trials:
        raise CalibrationError("INVALID_SUCCESSES")
    k = _positive_int(k, "INVALID_K")
    if trials < k:
        return PassKMeasure(NOT_MEASURED, None, trials, (REASON_N_BELOW_K,), k, successes)
    value = math.comb(successes, k) / math.comb(trials, k)
    return PassKMeasure(MEASURED, value, trials, (), k, successes)


def _base(measure: BrierMeasure | ECEMeasure | PassKMeasure) -> dict[str, Any]:
    return {
        "availability": measure.availability,
        "value": measure.value,
        "n": measure.n,
        "reason_codes": list(measure.reason_codes),
    }


def project_measure(measure: BrierMeasure | ECEMeasure | PassKMeasure) -> dict[str, Any]:
    """The read.v1 ``$defs.measure`` object for one view, plus that view's own inputs."""
    rendered = _base(measure)
    if isinstance(measure, ECEMeasure):
        rendered["bins"] = measure.bins
        rendered["min_bin_n"] = measure.min_bin_n
    elif isinstance(measure, PassKMeasure):
        rendered["k"] = measure.k
    return rendered


def project_calibration(
    brier_measure: BrierMeasure, ece_measure: ECEMeasure, pass_k_measure: PassKMeasure
) -> dict[str, Any]:
    """The read.v1 ``$defs.calibration`` object: the three views, each rendered above."""
    return {
        "brier": project_measure(brier_measure),
        "ece": project_measure(ece_measure),
        "pass_k": project_measure(pass_k_measure),
    }
