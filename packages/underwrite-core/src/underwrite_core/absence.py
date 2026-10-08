"""Fail-closed population checks with explicit, dated exemptions.

An empty population never passes without an applicable exemption. Callers supply
current time explicitly so expiry evaluation remains deterministic and pure."""

from dataclasses import dataclass
from datetime import date
from enum import Enum


class AbsenceError(ValueError):
    """Raised when a population-absence verdict cannot be computed as specified."""


class AbsenceOutcome(Enum):
    """Population-check outcomes, including explicit exemption states."""

    PASS = "PASS"
    EMPTY_POPULATION = "EMPTY_POPULATION"
    SKIPPED_WITH_REASON = "SKIPPED_WITH_REASON"
    EXEMPTION_EXPIRED = "EXEMPTION_EXPIRED"


@dataclass(frozen=True)
class Exemption:
    """A declared exemption with reason, expiry and optional decision reference."""

    reason: str
    expiry: str
    adr: str | None = None


@dataclass(frozen=True)
class AbsenceVerdict:
    """The outcome of one `check_population` call, plus the evidence behind it."""

    outcome: AbsenceOutcome
    count: int
    min_count: int
    reason: str | None
    expiry: str | None


def _validate_exemption(exemption: Exemption) -> date:
    """Return the exemption's parsed expiry, or raise `AbsenceError("INVALID_EXEMPTION")`."""
    if not exemption.reason.strip():
        raise AbsenceError("INVALID_EXEMPTION")
    try:
        return date.fromisoformat(exemption.expiry)
    except ValueError as exc:
        raise AbsenceError("INVALID_EXEMPTION") from exc


def check_population(
    count: int,
    min_count: int,
    *,
    exemption: Exemption | None = None,
    now: str | None = None,
) -> AbsenceVerdict:
    """Classify a population count against `min_count`.

    `count >= min_count` always passes. Otherwise an absent `exemption`
    fails as `EMPTY_POPULATION`; a present one requires a sealed-clock
    `now` (raises `AbsenceError("NOW_REQUIRED")` when missing) and must
    itself be well-formed (raises `AbsenceError("INVALID_EXEMPTION")`
    otherwise), then compares `expiry` to `now` via `date.fromisoformat`.
    """
    if count >= min_count:
        return AbsenceVerdict(
            outcome=AbsenceOutcome.PASS, count=count, min_count=min_count, reason=None, expiry=None
        )

    if exemption is None:
        return AbsenceVerdict(
            outcome=AbsenceOutcome.EMPTY_POPULATION,
            count=count,
            min_count=min_count,
            reason=None,
            expiry=None,
        )

    if now is None:
        raise AbsenceError("NOW_REQUIRED")

    expiry_date = _validate_exemption(exemption)
    now_date = date.fromisoformat(now)

    if expiry_date > now_date:
        outcome = AbsenceOutcome.SKIPPED_WITH_REASON
    else:
        outcome = AbsenceOutcome.EXEMPTION_EXPIRED
    return AbsenceVerdict(
        outcome=outcome,
        count=count,
        min_count=min_count,
        reason=exemption.reason,
        expiry=exemption.expiry,
    )
