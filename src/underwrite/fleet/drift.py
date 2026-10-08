"""Combine local pin verification with fail-closed population checks.

An empty active registry uses the empty-pins exemption key. Completely missing
configured checkouts use ci-no-siblings; without an applicable exemption they
remain unconfigured. Any changed pin fails the whole check. Retired pins never
inflate the active population. No exemption is created or loaded implicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from underwrite_core.absence import AbsenceError, AbsenceOutcome, Exemption, check_population

from underwrite.fleet.pins import PinReport, PinState, verify_pins


class DriftOutcome(Enum):
    """Combined verdict vocabulary for `drift_check`: pins.PinState's DRIFT/UNCONFIGURED
    folded together with underwrite_core.absence's outcome names."""

    PASS = "PASS"
    DRIFT = "DRIFT"
    EMPTY_POPULATION = "EMPTY_POPULATION"
    SKIPPED_WITH_REASON = "SKIPPED_WITH_REASON"
    EXEMPTION_EXPIRED = "EXEMPTION_EXPIRED"
    NOW_REQUIRED = "NOW_REQUIRED"
    UNCONFIGURED = "UNCONFIGURED"


# PASS/SKIPPED_WITH_REASON exit 0; DRIFT/EMPTY_POPULATION/EXEMPTION_EXPIRED/NOW_REQUIRED
# exit 1; UNCONFIGURED exits 2 (a reasoned skip -- distinct from both success and failure).
_EXIT_CODES: dict[DriftOutcome, int] = {
    DriftOutcome.PASS: 0,
    DriftOutcome.SKIPPED_WITH_REASON: 0,
    DriftOutcome.DRIFT: 1,
    DriftOutcome.EMPTY_POPULATION: 1,
    DriftOutcome.EXEMPTION_EXPIRED: 1,
    DriftOutcome.NOW_REQUIRED: 1,
    DriftOutcome.UNCONFIGURED: 2,
}


@dataclass(frozen=True)
class DriftVerdict:
    """The outcome of one `drift_check` call, plus the pin report and exemption evidence."""

    outcome: DriftOutcome
    pin_report: PinReport
    reason: str | None = None
    expiry: str | None = None
    adr: str | None = None

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self.outcome]


def _population_verdict(
    report: PinReport, exemption: Exemption | None, now: str | None, uncovered: DriftOutcome
) -> DriftVerdict:
    """Evaluate the shared count=0,min_count=1 absence rule and fold it into a `DriftVerdict`.

    `uncovered` is the outcome to use when no exemption covers the gap: callers pass
    EMPTY_POPULATION (the pins registry itself is empty) or UNCONFIGURED (every active
    manifest is UNCONFIGURED), so the same core rule serves two different branches
    without losing which one is which.
    """
    try:
        verdict = check_population(0, 1, exemption=exemption, now=now)
    except AbsenceError as exc:
        if exc.args[0] == "NOW_REQUIRED":
            return DriftVerdict(DriftOutcome.NOW_REQUIRED, report)
        raise
    if verdict.outcome is AbsenceOutcome.EMPTY_POPULATION:
        return DriftVerdict(uncovered, report)
    mapped = {
        AbsenceOutcome.SKIPPED_WITH_REASON: DriftOutcome.SKIPPED_WITH_REASON,
        AbsenceOutcome.EXEMPTION_EXPIRED: DriftOutcome.EXEMPTION_EXPIRED,
    }[verdict.outcome]
    adr = exemption.adr if exemption is not None else None
    return DriftVerdict(mapped, report, verdict.reason, verdict.expiry, adr)


def drift_check(
    pins_root: Path,
    siblings_root: Path,
    *,
    exemptions: Mapping[str, Exemption] | None = None,
    now: str | None = None,
) -> DriftVerdict:
    """Verify pins (`underwrite.fleet.pins.verify_pins`) and fold in the population rule.

    See the module docstring for the exemption-key/branch mapping.
    """
    exemptions = exemptions or {}
    report = verify_pins(pins_root, siblings_root)

    if report.active_population == 0:
        return _population_verdict(
            report, exemptions.get("empty-pins"), now, DriftOutcome.EMPTY_POPULATION
        )

    active_results = [r for r in report.results if r.state is not PinState.RETIRED]
    present_repos = {r.repo for r in active_results if r.state is not PinState.UNCONFIGURED}
    unconfigured_repos = {r.repo for r in active_results if r.state is PinState.UNCONFIGURED}
    any_drift = any(r.state is PinState.DRIFT for r in active_results)

    if not present_repos:
        return _population_verdict(
            report, exemptions.get("ci-no-siblings"), now, DriftOutcome.UNCONFIGURED
        )
    if any_drift:
        return DriftVerdict(DriftOutcome.DRIFT, report)
    if unconfigured_repos:
        return DriftVerdict(DriftOutcome.UNCONFIGURED, report)
    return DriftVerdict(DriftOutcome.PASS, report)
