"""Three exhaustive availability states of a required-instrument panel and the cap
each puts on the jury's full-panel read (evidence fusion; read.v1
``$defs.availability``). ``full`` leaves the read alone and records
``triggered: false``; ``partial`` (something missing, at least ``quorum``
reporting) keeps the full-panel score bit for bit, lowers only a ``pass`` to
``warn`` and turns escalation on; ``below_quorum`` is not a smaller panel's
read but no read at all -- the verdict is the string ``not_measured`` (the
const read.v1's not_measured branch requires) and the score is ``None`` (JSON
null), never zero, never recomputed. The reason code for that last state is the
``below_quorum``. What a downstream layer makes of a
read that was not taken is that layer's vocabulary, not this module's."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

AvailabilityState = Literal["full", "partial", "below_quorum"]
MeasuredVerdict = Literal["pass", "warn", "fail"]
CappedVerdict = MeasuredVerdict | Literal["not_measured"]
InstrumentLoss = Literal["missing", "error"]
ReasonCode = Literal["below_quorum", "required_instrument_missing", "required_instrument_error"]
AvailabilityErrorCode = Literal[
    "INVALID_INSTRUMENT",
    "DUPLICATE_INSTRUMENT",
    "INVALID_QUORUM",
    "QUORUM_UNREACHABLE",
    "UNKNOWN_INSTRUMENT",
    "UNKNOWN_LOSS",
    "CONTRADICTORY_LOSS",
    "INVALID_VERDICT",
    "INVALID_SCORE",
    "INVALID_ESCALATE",
]

MEASURED_VERDICTS: tuple[MeasuredVerdict, ...] = ("pass", "warn", "fail")
NOT_MEASURED: Literal["not_measured"] = "not_measured"
LOSSES: tuple[InstrumentLoss, ...] = ("missing", "error")
LOSS_REASON: dict[InstrumentLoss, ReasonCode] = {
    "missing": "required_instrument_missing",
    "error": "required_instrument_error",
}


class AvailabilityInputError(ValueError):
    """Malformed policy, panel or read; no state is produced."""

    def __init__(self, code: AvailabilityErrorCode, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


@dataclass(frozen=True)
class QuorumPolicy:
    """The explicit policy inputs (measurement_policy.v1): a set of names and a floor."""

    required_instruments: tuple[str, ...]
    quorum: int


@dataclass(frozen=True)
class PanelRead:
    """The jury's read on the full panel, before any cap."""

    verdict: MeasuredVerdict
    score: float
    escalate: bool


@dataclass(frozen=True)
class Availability:
    """Which required instruments reported, how the rest were lost, and the resulting state."""

    required_instruments: tuple[str, ...]
    quorum: int
    available: tuple[str, ...]
    missing: tuple[str, ...]
    losses: tuple[tuple[str, InstrumentLoss], ...]
    state: AvailabilityState
    triggered: bool


@dataclass(frozen=True)
class CappedRead:
    """The read after the cap: ``not_measured`` with no score below quorum, otherwise the
    panel's own verdict and numbers."""

    verdict: CappedVerdict
    score: float | None
    escalate: bool
    reason_codes: tuple[ReasonCode, ...]


def _instrument_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise AvailabilityInputError("INVALID_INSTRUMENT", repr(value))
    return value


def _unique_sorted(names: Iterable[object]) -> tuple[str, ...]:
    return tuple(sorted({_instrument_name(name) for name in names}))


def make_policy(required_instruments: Iterable[object], quorum: object) -> QuorumPolicy:
    """Validate the two policy inputs. A quorum the set can never reach is a configuration error."""
    required = tuple(_instrument_name(name) for name in required_instruments)
    if len(set(required)) != len(required):
        raise AvailabilityInputError("DUPLICATE_INSTRUMENT", repr(required))
    if isinstance(quorum, bool) or not isinstance(quorum, int) or quorum < 1:
        raise AvailabilityInputError("INVALID_QUORUM", repr(quorum))
    if quorum > len(required):
        raise AvailabilityInputError("QUORUM_UNREACHABLE", f"{quorum} > {len(required)}")
    return QuorumPolicy(required_instruments=tuple(sorted(required)), quorum=quorum)


def make_panel_read(verdict: object, score: object, escalate: object) -> PanelRead:
    """Validate the jury's full-panel read: a measured verdict, a finite score in [0, 1], a flag."""
    if verdict not in MEASURED_VERDICTS:
        raise AvailabilityInputError("INVALID_VERDICT", repr(verdict))
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise AvailabilityInputError("INVALID_SCORE", repr(score))
    if not 0.0 <= score <= 1.0:
        raise AvailabilityInputError("INVALID_SCORE", repr(score))
    if not isinstance(escalate, bool):
        raise AvailabilityInputError("INVALID_ESCALATE", repr(escalate))
    return PanelRead(verdict=verdict, score=float(score), escalate=escalate)


def _known_losses(
    policy: QuorumPolicy, available: tuple[str, ...], known_losses: Mapping[object, object]
) -> dict[str, InstrumentLoss]:
    losses: dict[str, InstrumentLoss] = {}
    for name, loss in known_losses.items():
        instrument = _instrument_name(name)
        if instrument not in policy.required_instruments:
            raise AvailabilityInputError("UNKNOWN_INSTRUMENT", instrument)
        if loss not in LOSSES:
            raise AvailabilityInputError("UNKNOWN_LOSS", f"{instrument}: {loss!r}")
        if instrument in available:
            raise AvailabilityInputError("CONTRADICTORY_LOSS", instrument)
        losses[instrument] = loss
    return losses


def classify_loss(states: Iterable[object]) -> InstrumentLoss:
    """How a required instrument that did not report was lost: ``error`` if any of its
    states is ``error``, otherwise ``missing``."""
    return "error" if "error" in states else "missing"


def assess(
    policy: QuorumPolicy, reported: Iterable[object], known_losses: Mapping[object, object]
) -> Availability:
    """Classify the panel. ``reported`` lists instruments that delivered a measured signal (any
    order, repeats allowed); ``known_losses`` says how a missing instrument was lost when the caller
    knows (``missing`` or ``error``); an unexplained absence is ``missing``."""
    available = _unique_sorted(reported)
    for instrument in available:
        if instrument not in policy.required_instruments:
            raise AvailabilityInputError("UNKNOWN_INSTRUMENT", instrument)
    losses = _known_losses(policy, available, known_losses)
    missing = tuple(name for name in policy.required_instruments if name not in available)
    if not missing:
        state: AvailabilityState = "full"
    elif len(available) >= policy.quorum:
        state = "partial"
    else:
        state = "below_quorum"
    return Availability(
        required_instruments=policy.required_instruments,
        quorum=policy.quorum,
        available=available,
        missing=missing,
        losses=tuple((name, losses.get(name, "missing")) for name in missing),
        state=state,
        triggered=state != "full",
    )


def loss_reasons(availability: Availability) -> tuple[ReasonCode, ...]:
    """The distinct loss reason codes, sorted: the one order every read emits them in, the
    cap's and the read's panel-less not_measured branch alike."""
    return tuple(sorted({LOSS_REASON[loss] for _name, loss in availability.losses}))


def cap(read: PanelRead, availability: Availability) -> CappedRead:
    """Apply the cap: one branch per state, none shared."""
    if availability.state == "full":
        return CappedRead(read.verdict, read.score, read.escalate, ())
    if availability.state == "partial":
        verdict: MeasuredVerdict = "warn" if read.verdict == "pass" else read.verdict
        return CappedRead(verdict, read.score, True, loss_reasons(availability))
    return CappedRead(NOT_MEASURED, None, True, ("below_quorum", *loss_reasons(availability)))


def project(availability: Availability) -> dict[str, object]:
    """The read.v1 ``$defs.availability`` object, exactly its required keys."""
    return {
        "required_instruments": list(availability.required_instruments),
        "quorum": availability.quorum,
        "available": list(availability.available),
        "missing": list(availability.missing),
        "state": availability.state,
        "triggered": availability.triggered,
    }


def project_capped(capped: CappedRead) -> dict[str, object]:
    """The capped read's contribution to the top-level read.v1 keys: ``verdict`` (a measured
    verdict or the string ``not_measured``), ``score``, ``escalate``, ``reason_codes``."""
    return {
        "verdict": capped.verdict,
        "score": capped.score,
        "escalate": capped.escalate,
        "reason_codes": list(capped.reason_codes),
    }
