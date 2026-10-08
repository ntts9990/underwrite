"""Fuse measured signals into a confidence-weighted jury read.

Unavailable signals do not vote. Entropy and disagreement describe the measured
panel; absence remains explicit rather than becoming a passing signal."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, cast

Availability = Literal["measured", "not_measured"]
Verdict = Literal["pass", "warn", "fail"]
Combine = Literal["mean", "product"]

VERDICTS: tuple[Verdict, ...] = ("pass", "warn", "fail")
COMBINES: tuple[Combine, ...] = ("mean", "product")
REASON_SIGNAL_NOT_MEASURED = "signal_not_measured"
REASON_NO_OBSERVATIONS = "no_observations"


class JuryError(ValueError):
    """A malformed signal or a fusion the caller has not justified."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class Signal:
    """One (method, instrument) reading in the read.v1 signal shape; fields only."""

    method: str
    instrument: str
    availability: Availability
    verdict: Verdict | None
    score: float | None
    confidence: float
    e_value: float | None
    null_id: str | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class JuryResult:
    """The fusion outcome plus every input signal, untouched and in input order."""

    combine: Combine
    availability: Availability
    e_value: float | None
    disagreement: float | None
    members: int
    independence: str | None
    reason_codes: tuple[str, ...]
    signals: tuple[Signal, ...]


def _is_unit(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _check_common(signal: Signal) -> None:
    if not signal.method:
        raise JuryError("EMPTY_METHOD")
    if not signal.instrument:
        raise JuryError("EMPTY_INSTRUMENT")
    if signal.availability not in ("measured", "not_measured"):
        raise JuryError("UNKNOWN_AVAILABILITY", repr(signal.availability))
    if not _is_unit(signal.confidence):
        raise JuryError("CONFIDENCE_OUT_OF_RANGE", repr(signal.confidence))
    codes = signal.reason_codes
    if any(not code for code in codes):
        raise JuryError("MALFORMED_REASON_CODES", repr(codes))
    if len(set(codes)) != len(codes):
        raise JuryError("DUPLICATE_REASON_CODES", repr(codes))


def _check_measured(signal: Signal) -> None:
    if signal.verdict not in VERDICTS:
        raise JuryError("UNKNOWN_VERDICT", repr(signal.verdict))
    if not _is_unit(signal.score):
        raise JuryError("SCORE_OUT_OF_RANGE", repr(signal.score))
    e_value = signal.e_value
    if e_value is None:
        return
    if isinstance(e_value, bool) or not math.isfinite(e_value) or e_value < 0.0:
        raise JuryError("E_VALUE_OUT_OF_RANGE", repr(e_value))
    if not signal.null_id:
        raise JuryError("E_VALUE_WITHOUT_NULL_ID", signal.method)


def _check_unmeasured(signal: Signal) -> None:
    if signal.verdict is not None or signal.score is not None or signal.e_value is not None:
        raise JuryError("UNMEASURED_SIGNAL_CARRIES_VALUES", signal.method)
    if not signal.reason_codes:
        raise JuryError("UNMEASURED_SIGNAL_WITHOUT_REASON", signal.method)


def check_signal(signal: Signal) -> Signal:
    """Return `signal` if it is a well-formed read.v1 signal, else raise JuryError."""
    _check_common(signal)
    if signal.availability == "measured":
        _check_measured(signal)
    else:
        _check_unmeasured(signal)
    return signal


def disagreement(signals: Sequence[Signal]) -> float:
    """Normalised Shannon entropy of the confidence-weighted verdicts of the measured signals.

    0 is full agreement (or nothing to weigh), 1 is the three verdicts equally weighted;
    the convention, entropy over log(3). Each verdict's weight is a correctly
    rounded sum, so the order of the signals cannot reach the last bit. The signals are
    checked ones (`check_signal`), so every weight is non-negative: a positive weight
    implies a positive total, and a zero total leaves no share to divide. A share is kept
    only if it is still positive after the division -- a subnormal weight over a large enough
    total underflows to 0.0, whose limit contribution p·log p is 0, and log(0) is undefined.
    """
    weights = [
        math.fsum(s.confidence for s in signals if s.availability == "measured" and s.verdict == v)
        for v in VERDICTS
    ]
    total = math.fsum(weights)
    shares = [
        share for share in (weight / total for weight in weights if weight > 0.0) if share > 0.0
    ]
    if len(shares) <= 1:
        return 0.0
    entropy = -math.fsum(share * math.log(share) for share in shares)
    return entropy / math.log(len(VERDICTS))


def measured_instruments(signals: Sequence[Signal]) -> tuple[str, ...]:
    """Sorted, de-duplicated instruments that delivered a measured signal."""
    return tuple(sorted({s.instrument for s in signals if s.availability == "measured"}))


def _check_combine(combine: str, independence: str | None) -> None:
    if combine not in COMBINES:
        raise JuryError("UNKNOWN_COMBINE", repr(combine))
    if combine == "product":
        if independence is None or not independence.strip():
            raise JuryError("INDEPENDENCE_NOT_STATED", "product fusion needs its evidence")
    elif independence is not None:
        raise JuryError("INDEPENDENCE_WITHOUT_PRODUCT", "mean fusion needs no independence")


def _fusable(signals: Sequence[Signal], roster: Sequence[tuple[str, str]]) -> list[Signal]:
    expected: set[tuple[str, str]] = set()
    for entry in cast("Sequence[object]", roster):
        match entry:
            case (str(method), str(instrument)) if method and instrument:
                identity = (method, instrument)
            case _:
                raise JuryError("MALFORMED_E_VALUE_ROSTER", repr(entry))
        if identity in expected:
            raise JuryError("DUPLICATE_ROSTER_SIGNAL")
        expected.add(identity)
    submitted = [(s.method, s.instrument) for s in signals]
    if len(set(submitted)) != len(submitted):
        raise JuryError("DUPLICATE_SIGNAL")
    fusable = [s for s in signals if s.availability == "measured" and s.e_value is not None]
    present = {(s.method, s.instrument) for s in fusable}
    if present - expected:
        raise JuryError("UNDECLARED_E_VALUE", repr(sorted(present - expected)))
    null_ids = {s.null_id for s in fusable}
    if len(null_ids) > 1:
        raise JuryError("NULL_ID_MISMATCH", repr(sorted(str(n) for n in null_ids)))
    return fusable if present == expected else []


def _combined(values: Sequence[float], combine: Combine) -> float:
    ordered = sorted(values)
    if combine == "mean":
        try:
            fused = math.fsum(ordered) / len(ordered)
        except OverflowError:
            # A finite nonnegative mean cannot exceed its largest input. Only
            # the intermediate sum overflowed; avoid scaling away subnormals.
            fused = float(sum((Fraction(v) for v in ordered), Fraction()) / len(ordered))
    else:
        fused = math.prod(ordered)
    if not math.isfinite(fused):
        raise JuryError("E_VALUE_OVERFLOW", repr(ordered))
    return fused


def _unmeasured_result(
    signals: tuple[Signal, ...], combine: Combine, independence: str | None
) -> JuryResult:
    reason = REASON_NO_OBSERVATIONS if not signals else REASON_SIGNAL_NOT_MEASURED
    return JuryResult(combine, "not_measured", None, None, 0, independence, (reason,), signals)


def fuse(
    signals: Sequence[Signal],
    *,
    combine: Combine,
    independence: str | None,
    e_value_roster: Sequence[tuple[str, str]],
) -> JuryResult:
    """Fuse a complete, predeclared roster of (method, instrument) e-values.

    The caller must fix the roster before outcomes and bind it to subject/policy;
    this pure function cannot prove that history. Mean needs no independence, but
    still requires valid same-null e-values and no outcome-dependent selection.
    Product additionally requires stated independence (not proven by its string).
    Missing roster members withhold fusion, never renormalize survivors. Other
    descriptive signals remain visible but cannot contribute undeclared e-values.
    """
    _check_combine(combine, independence)
    kept = tuple(check_signal(signal) for signal in signals)
    fusable = _fusable(kept, e_value_roster)
    if not fusable:
        return _unmeasured_result(kept, combine, independence)
    fused = _combined([s.e_value for s in fusable if s.e_value is not None], combine)
    absent = any(s.availability == "not_measured" for s in kept)
    codes = (REASON_SIGNAL_NOT_MEASURED,) if absent else ()
    return JuryResult(
        combine=combine,
        availability="measured",
        e_value=fused,
        disagreement=disagreement(kept),
        members=len(fusable),
        independence=independence,
        reason_codes=codes,
        signals=kept,
    )


def project_signal(signal: Signal) -> dict[str, object]:
    """The read.v1 `$defs.signal` object for one signal."""
    return {
        "method": signal.method,
        "instrument": signal.instrument,
        "availability": signal.availability,
        "verdict": signal.verdict,
        "score": signal.score,
        "confidence": signal.confidence,
        "e_value": signal.e_value,
        "null_id": signal.null_id,
        "reason_codes": list(signal.reason_codes),
    }


def project(result: JuryResult) -> dict[str, object]:
    """The read.v1 `$defs.jury` object for a fusion result (signals are projected separately)."""
    return {
        "combine": result.combine,
        "availability": result.availability,
        "e_value": result.e_value,
        "disagreement": result.disagreement,
        "members": result.members,
        "independence": result.independence,
        "reason_codes": list(result.reason_codes),
    }
