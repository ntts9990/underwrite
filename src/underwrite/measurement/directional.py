"""Card: measurement-semantics | Paper: ADR-020 | Stage: semantic-repair.

Directional stopping over a complete, predeclared binary event population.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from underwrite.measurement.eprocess import EProcessPolicy, wealths
from underwrite.measurement_contract.read import crossing_status
from underwrite.measurement_contract.semantics import MAX_CASES, THRESHOLD, integer, number, require


def run_directional(
    ordered_outcomes: Sequence[int],
    *,
    p0: float,
    max_observations: int | None,
    desirable_direction: str,
) -> dict[str, Any]:
    """Stop at the first side crossing; the caller owns assumption eligibility."""
    require(0 < number(p0, 0, 1) < 1)
    require(desirable_direction in ("higher", "lower"))
    require(0 < len(ordered_outcomes) <= MAX_CASES)
    for outcome in ordered_outcomes:
        integer(outcome, 0, 1)
    if max_observations is not None:
        integer(max_observations, 1, 2**53 - 1)
    policy = EProcessPolicy(p0, 1, 1, THRESHOLD, max_observations, None, "declared-event")
    successes = consumed = 0
    upper = lower = 1.0
    stop = "end_of_population"
    for outcome in ordered_outcomes:
        consumed += 1
        successes += outcome
        upper, lower, _ = wealths(policy, successes, consumed - successes)
        status = crossing_status(upper, lower, desirable_direction)
        if status != "inconclusive":
            stop = (
                "conflicting_crossing"
                if status == "withheld"
                else "upper_crossing"
                if upper >= THRESHOLD
                else "lower_crossing"
            )
            break
        if max_observations is not None and consumed == max_observations:
            stop = "budget_observations"
            break
    return {
        "availability": "measured",
        "e_upper": upper,
        "e_lower": lower,
        "consumed_count": consumed,
        "successes": successes,
        "failures": consumed - successes,
        "unused_observed_count": len(ordered_outcomes) - consumed,
        "stop_reason": stop,
    }
