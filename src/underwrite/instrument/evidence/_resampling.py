"""Explicit shared resampling policy and versioned linear percentile extraction.
No configuration reads or implicit seed; callers distinguish missing estimates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

MIN_BOOTSTRAP_ITERATIONS = 1000
MAX_BOOTSTRAP_ITERATIONS = 10000


class ResamplingError(ValueError):
    """Invalid explicit policy or unavailable finite percentile arithmetic."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ResamplingPolicy:
    """Injected settings; this record does not assert canonical configuration."""

    seed: int
    bootstrap_iterations: int
    confidence: float


def _confidence(value: float) -> None:
    if type(value) is not float or not 0 < value < 1:
        raise ResamplingError("INVALID_CONFIDENCE")


def validate_policy(policy: ResamplingPolicy) -> None:
    """Validate constructed records again, including exact runtime scalar types."""
    if type(policy) is not ResamplingPolicy:
        raise ResamplingError("INVALID_RESAMPLING_POLICY")
    if type(policy.seed) is not int:
        raise ResamplingError("INVALID_SEED")
    iterations = policy.bootstrap_iterations
    if type(iterations) is not int or not (
        MIN_BOOTSTRAP_ITERATIONS <= iterations <= MAX_BOOTSTRAP_ITERATIONS
    ):
        raise ResamplingError("INVALID_BOOTSTRAP_ITERATIONS")
    _confidence(policy.confidence)


def _endpoint(samples: list[float], rank: float) -> float:
    low = math.floor(rank)
    high = min(low + 1, len(samples) - 1)
    weight = Fraction(rank - low)
    # Exact binary endpoint arithmetic avoids overflowing high-low or its sum.
    result = float((1 - weight) * Fraction(samples[low]) + weight * Fraction(samples[high]))
    if not math.isfinite(result):
        raise ResamplingError("NONFINITE_PERCENTILE")
    return result


def percentile_interval(
    samples: list[float] | tuple[float, ...], *, confidence: float
) -> tuple[float, float]:
    """Return percentile-linear-v1 using symmetric, explicitly ordered ranks.

    This is not source discrete-rank or BCa parity. Sorting owns a fresh list;
    the two output endpoints own exact binary interpolation followed by one
    final float conversion. Confidence is a binary float, not exact decimal.
    """
    _confidence(confidence)
    if type(samples) not in (list, tuple) or not samples:
        raise ResamplingError("INVALID_PERCENTILE_POPULATION")
    if any(type(value) is not float or not math.isfinite(value) for value in samples):
        raise ResamplingError("INVALID_PERCENTILE_SAMPLE")
    ordered = sorted(samples)
    tail = (1.0 - confidence) / 2.0
    last = len(ordered) - 1
    low_rank = last * tail
    high_rank = float(last) - low_rank
    return _endpoint(ordered, low_rank), _endpoint(ordered, high_rank)
