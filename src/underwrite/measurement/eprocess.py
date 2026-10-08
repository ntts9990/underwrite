"""Betting e-process over binary evidence against an explicit simple null.

Two one-sided beta-mixture wealths are updated separately; their equal mixture
is the reported e-value. Zero wealth closes the stream as unmeasured. The engine
never resets or declares failure. Replay restoration checks a saved projection
against separately supplied observations and policy; parsing alone proves neither
truth nor provenance."""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard, cast

METHOD = "beta_mixture_eprocess"
# measurement_policy.v2 consts; tests/gates/test_s5_statistical_contract.py pins the copies.
CONTRACT_P0 = 0.5
CONTRACT_PASS_E = 20.0
CONTRACT_PRIOR_A = 1
CONTRACT_PRIOR_B = 1
WEALTH_ABSORBED = "wealth_absorbed"
STOP_REASONS: tuple[str, ...] = (
    "complete",
    "threshold",
    "budget_observations",
    "budget_cost",
    "absorbed",
)
MEASURED = "measured"
NOT_MEASURED = "not_measured"
Verdict = Literal["pass", "warn"]
_SCORE_DIGITS = 6
_LOG_FLOAT_MAX = math.log(sys.float_info.max)
_PROJECTION_FIELDS: tuple[str, ...] = (
    "method",
    "availability",
    "e_value",
    "e_upper",
    "e_lower",
    "pass_e",
    "stop_reason",
    "stopped_sequence",
    "observations_used",
    "observations_total",
    "trials_used",
    "cost_used",
    "max_observations",
    "max_cost",
    "null_id",
    "reason_codes",
)


class EProcessError(ValueError):
    """A malformed policy, observation or projection; never a measurement outcome."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class EProcessPolicy:
    """Every parameter of one run, supplied by the caller; nothing here has a default."""

    p0: float
    prior_a: int
    prior_b: int
    pass_e: float
    max_observations: int | None
    max_cost: float | None
    null_id: str


@dataclass(frozen=True)
class Observation:
    """One sequenced evidence record: `successes` of `trials`, with an optional cost."""

    successes: int
    trials: int
    cost: float | None


@dataclass(frozen=True)
class EProcessRun:
    """The closed engine state of one stream: counts, wealths and why it stopped."""

    policy: EProcessPolicy
    successes: int
    trials: int
    observations_used: int
    observations_total: int
    stopped_sequence: int
    cost_used: float
    e_upper: float
    e_lower: float
    e_value: float
    stop_reason: str


@dataclass(frozen=True)
class EProcessProjection:
    """Exactly the read.v1 `$defs.eprocess` fields; the only form that leaves this module."""

    method: str
    availability: str
    e_value: float
    e_upper: float
    e_lower: float
    pass_e: float
    stop_reason: str
    stopped_sequence: int
    observations_used: int
    observations_total: int
    trials_used: int
    cost_used: float
    max_observations: int | None
    max_cost: float | None
    null_id: str
    reason_codes: tuple[str, ...]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite(value: object) -> bool:
    try:
        return (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
    except OverflowError:
        return False


def check_policy(policy: EProcessPolicy) -> EProcessPolicy:
    """Refuse a policy the engine could not run honestly; return it unchanged otherwise."""
    if not _finite(policy.p0) or not 0.0 < policy.p0 < 1.0:
        raise EProcessError("P0_OUT_OF_RANGE", repr(policy.p0))
    if not _is_int(policy.prior_a) or not _is_int(policy.prior_b):
        raise EProcessError("PRIOR_NOT_INTEGER", f"a={policy.prior_a!r}, b={policy.prior_b!r}")
    if policy.prior_a < 1 or policy.prior_b < 1:
        raise EProcessError("PRIOR_NOT_POSITIVE", f"a={policy.prior_a!r}, b={policy.prior_b!r}")
    if not _finite(policy.pass_e) or policy.pass_e < 1.0:
        raise EProcessError("PASS_E_BELOW_ONE", repr(policy.pass_e))
    _check_budgets(policy.max_observations, policy.max_cost)
    if not isinstance(cast("object", policy.null_id), str) or not policy.null_id:
        raise EProcessError("NULL_ID_EMPTY", repr(policy.null_id))
    return policy


def _check_budgets(max_observations: int | None, max_cost: float | None) -> None:
    if max_observations is not None and (not _is_int(max_observations) or max_observations < 1):
        raise EProcessError("MAX_OBSERVATIONS_NOT_POSITIVE", repr(max_observations))
    if max_cost is not None and (not _finite(max_cost) or max_cost <= 0.0):
        raise EProcessError("MAX_COST_NOT_POSITIVE", repr(max_cost))


def check_observation(observation: Observation) -> Observation:
    """Refuse an observation without at least one trial or with an unusable count/cost."""
    if not _is_int(observation.trials) or observation.trials < 1:
        raise EProcessError("TRIALS_NOT_POSITIVE", repr(observation.trials))
    if not _is_int(observation.successes) or not 0 <= observation.successes <= observation.trials:
        raise EProcessError("SUCCESSES_OUT_OF_RANGE", repr(observation.successes))
    if observation.cost is not None and (not _finite(observation.cost) or observation.cost < 0.0):
        raise EProcessError("COST_NEGATIVE", repr(observation.cost))
    return observation


def binary_observations(
    outcomes: Sequence[int], costs: Sequence[float | None] | None
) -> tuple[Observation, ...]:
    """One trial per outcome (0 or 1); costs, when given, align one-to-one."""
    if costs is not None and len(costs) != len(outcomes):
        raise EProcessError("COSTS_MISALIGNED", f"{len(costs)} costs for {len(outcomes)} outcomes")
    records: list[Observation] = []
    for index, outcome in enumerate(outcomes):
        if not _is_int(outcome) or outcome not in (0, 1):
            raise EProcessError("OUTCOME_NOT_BINARY", repr(outcome))
        cost = None if costs is None else costs[index]
        records.append(check_observation(Observation(outcome, 1, cost)))
    return tuple(records)


def _log_mass(lower: int, upper: int, count: int, log_p: float, log_q: float) -> float:
    """log of sum_{j=lower}^{upper} C(count, j) p^j q^(count-j), summed without cancellation.

    log C(count, j) comes from lgamma, not from the integer binomial: the integer form costs a
    big-integer product per term, which made a stream of n updates cubic in n (1,000 binary
    observations took seconds). lgamma keeps every term O(1) at a relative error near 1e-12
    for counts in the thousands, far inside the 1e-9 parity target.
    """
    log_count = math.lgamma(count + 1)
    terms = [
        log_count
        - math.lgamma(j + 1)
        - math.lgamma(count - j + 1)
        + j * log_p
        + (count - j) * log_q
        for j in range(lower, upper + 1)
    ]
    peak = max(terms)
    return peak + math.log(math.fsum(math.exp(term - peak) for term in terms))


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def wealths(policy: EProcessPolicy, successes: int, failures: int) -> tuple[float, float, float]:
    """(e_upper, e_lower, e_value): the Beta(a, b) mixture restricted to each side of p0,
    each normalized by its own prior mass, and their equal mixture.

    For integer priors the truncated Beta integrals are binomial tails, so both sides are
    positive sums evaluated in log space; at p0 = 1/2 with a = b = 1 the equal mixture is
    numerically the full-interval mixture.

    The full mixture is the prior-mass-weighted average of the two sides, so the equal mixture
    is at least half of it; with a = b = 1 the full mixture is 1 / ((n + 1) C(n, s) p0^s q0^f)
    >= 1 / (n + 1). A stream under the contract priors therefore never drives e_value to 0.
    """
    a, b = policy.prior_a, policy.prior_b
    log_p, log_q = math.log(policy.p0), math.log1p(-policy.p0)
    count, lower_from = a + b + successes + failures - 1, a + successes
    prior_count, prior_from = a + b - 1, a
    log_ratio = _log_beta(a + successes, b + failures) - _log_beta(a, b)
    log_likelihood = successes * log_p + failures * log_q
    log_lower = (
        log_ratio
        + _log_mass(lower_from, count, count, log_p, log_q)
        - _log_mass(prior_from, prior_count, prior_count, log_p, log_q)
        - log_likelihood
    )
    log_upper = (
        log_ratio
        + _log_mass(0, lower_from - 1, count, log_p, log_q)
        - _log_mass(0, prior_from - 1, prior_count, log_p, log_q)
        - log_likelihood
    )
    if max(log_lower, log_upper) > _LOG_FLOAT_MAX:
        raise EProcessError("E_VALUE_OVERFLOW", f"s={successes}, f={failures}")
    e_upper, e_lower = math.exp(log_upper), math.exp(log_lower)
    return e_upper, e_lower, (e_upper + e_lower) / 2.0


def stop_after_update(e_value: float, pass_e: float) -> str | None:
    """Absorbed at wealth 0 or stopped at the first crossing; otherwise keep going.

    The only transition rule a stream uses. Under the contract priors `wealths` bounds e_value
    below by 1 / (2(n + 1)), so absorption is the rule for a wealth that underflows, not a
    state a stream reaches; it is exercised here and in `close_run` directly.
    """
    if e_value == 0.0:
        return "absorbed"
    if e_value >= pass_e:
        return "threshold"
    return None


def _stop_before_consuming(
    policy: EProcessPolicy, observations_used: int, cost_used: float, cost: float
) -> str | None:
    if policy.max_observations is not None and observations_used >= policy.max_observations:
        return "budget_observations"
    if policy.max_cost is not None and cost_used + cost > policy.max_cost:
        return "budget_cost"
    return None


def close_run(
    policy: EProcessPolicy, counts: tuple[int, int, int, int, int, float], reason: str
) -> EProcessRun:
    """Close a stream: `counts` is (successes, trials, used, total, stopped_sequence, cost_used).

    Every stop goes through here. A stream that consumed no trial -- empty, or stopped by a
    budget before its first observation -- is an error: read.v1 has no shape for
    a stream that measured nothing. Absorption zeroes all three wealths.
    """
    successes, trials, used, total, sequence, cost_used = counts
    if trials == 0:
        raise EProcessError("NO_TRIALS", "a stream needs at least one consumed trial")
    e_upper, e_lower, e_value = wealths(policy, successes, trials - successes)
    if reason == "absorbed":
        e_upper = e_lower = e_value = 0.0
    return EProcessRun(
        policy,
        successes,
        trials,
        used,
        total,
        sequence,
        cost_used,
        e_upper,
        e_lower,
        e_value,
        reason,
    )


def run_stream(observations: Sequence[Observation], policy: EProcessPolicy) -> EProcessRun:
    """Feed the observations in order and stop at the first crossing, a budget, absorption,
    or the end of the stream (`complete`). The minimal driver; no I/O, no state outside."""
    check_policy(policy)
    records = tuple(check_observation(observation) for observation in observations)
    if policy.max_cost is not None and any(record.cost is None for record in records):
        raise EProcessError("COST_REQUIRED", "max_cost set but an observation has no cost")
    successes = trials = used = 0
    cost_used = 0.0
    total = len(records)
    for sequence, observation in enumerate(records, start=1):
        cost = observation.cost or 0.0
        reason = _stop_before_consuming(policy, used, cost_used, cost)
        if reason is not None:
            return close_run(
                policy, (successes, trials, used, total, sequence - 1, cost_used), reason
            )
        successes += observation.successes
        trials += observation.trials
        used += 1
        cost_used += cost
        if not math.isfinite(cost_used):
            raise EProcessError("COST_OVERFLOW", "consumed cumulative cost is not finite")
        _e_upper, _e_lower, e_value = wealths(policy, successes, trials - successes)
        reason = stop_after_update(e_value, policy.pass_e)
        if reason is not None:
            return close_run(policy, (successes, trials, used, total, sequence, cost_used), reason)
    return close_run(policy, (successes, trials, used, total, total, cost_used), "complete")


def projection_of(run: EProcessRun) -> EProcessProjection:
    """The read.v1 view of a closed run; absorption is the only not-measured stop."""
    absorbed = run.stop_reason == "absorbed"
    return EProcessProjection(
        method=METHOD,
        availability=NOT_MEASURED if absorbed else MEASURED,
        e_value=run.e_value,
        e_upper=run.e_upper,
        e_lower=run.e_lower,
        pass_e=run.policy.pass_e,
        stop_reason=run.stop_reason,
        stopped_sequence=run.stopped_sequence,
        observations_used=run.observations_used,
        observations_total=run.observations_total,
        trials_used=run.trials,
        cost_used=run.cost_used,
        max_observations=run.policy.max_observations,
        max_cost=run.policy.max_cost,
        null_id=run.policy.null_id,
        reason_codes=(WEALTH_ABSORBED,) if absorbed else (),
    )


def project(projection: EProcessProjection) -> dict[str, Any]:
    """Render a checked projection as the read.v1 `$defs.eprocess` object, keys in order."""
    check_projection(projection)
    payload = {name: getattr(projection, name) for name in _PROJECTION_FIELDS}
    payload["reason_codes"] = list(projection.reason_codes)
    return payload


def _is_list(value: object) -> TypeGuard[list[object] | tuple[object, ...]]:
    return isinstance(value, (list, tuple))


def parse_projection(payload: Mapping[str, Any]) -> EProcessProjection:
    """Decode an untrusted projection and check internal consistency, not authenticity.

    Jointly rewritten fields may be consistent. Use restore with independently
    supplied observations/policy before treating a received projection as replayed.
    """
    keys = set(payload)
    expected = set(_PROJECTION_FIELDS)
    if keys != expected:
        raise EProcessError(
            "PROJECTION_FIELDS",
            f"extra={sorted(keys - expected)}, missing={sorted(expected - keys)}",
        )
    reasons: object = payload["reason_codes"]
    if not _is_list(reasons):
        raise EProcessError("REASON_CODES_NOT_LIST", repr(reasons))
    items = tuple(reasons)
    codes = tuple(code for code in items if isinstance(code, str))
    if len(codes) != len(items):
        raise EProcessError("REASON_CODES_NOT_STRINGS", repr(items))
    projection = EProcessProjection(
        **{name: payload[name] for name in _PROJECTION_FIELDS if name != "reason_codes"},
        reason_codes=codes,
    )
    return check_projection(projection)


def restore(
    payload: Mapping[str, Any], *, observations: Sequence[Observation], policy: EProcessPolicy
) -> EProcessProjection:
    """Verify equality with replay of separately supplied observations and policy.

    Callers own subject/source binding and input trust. Equal lossy projections
    do not prove historical identity, independent verification, or approval.
    """
    projection = parse_projection(payload)
    replayed = projection_of(run_stream(observations, policy))
    if projection != replayed:
        raise EProcessError("PROJECTION_REPLAY_MISMATCH", "projection differs from supplied inputs")
    return replayed


def check_projection(projection: EProcessProjection) -> EProcessProjection:
    """Every invariant the read.v1 block and impose, re-checked on the record."""
    _check_projection_labels(projection)
    _check_projection_numbers(projection)
    _check_projection_budgets(projection)
    _check_projection_wealth(projection)
    return projection


def _check_projection_labels(projection: EProcessProjection) -> None:
    if projection.method != METHOD:
        raise EProcessError("METHOD_MISMATCH", repr(projection.method))
    if projection.stop_reason not in STOP_REASONS:
        raise EProcessError("STOP_REASON_UNKNOWN", repr(projection.stop_reason))
    if not isinstance(cast("object", projection.null_id), str) or not projection.null_id:
        raise EProcessError("NULL_ID_EMPTY", repr(projection.null_id))
    absorbed = projection.stop_reason == "absorbed"
    expected_availability = NOT_MEASURED if absorbed else MEASURED
    if projection.availability != expected_availability:
        raise EProcessError(
            "AVAILABILITY_MISMATCH", f"{projection.stop_reason}: {projection.availability!r}"
        )
    expected_reasons = (WEALTH_ABSORBED,) if absorbed else ()
    if tuple(projection.reason_codes) != expected_reasons:
        raise EProcessError("REASON_CODES_MISMATCH", repr(projection.reason_codes))


def _check_projection_numbers(projection: EProcessProjection) -> None:
    for name in ("e_value", "e_upper", "e_lower", "cost_used"):
        value = getattr(projection, name)
        if not _finite(value) or value < 0.0:
            raise EProcessError("NUMBER_NOT_NONNEGATIVE_FINITE", f"{name}={value!r}")
    if not _finite(projection.pass_e) or projection.pass_e < 1.0:
        raise EProcessError("PASS_E_BELOW_ONE", repr(projection.pass_e))
    for name, floor in (
        ("stopped_sequence", 0),
        ("observations_used", 1),
        ("observations_total", 1),
        ("trials_used", 1),
    ):
        value = getattr(projection, name)
        if not _is_int(value) or value < floor:
            raise EProcessError("COUNT_OUT_OF_RANGE", f"{name}={value!r}")
    if projection.observations_total < projection.observations_used:
        raise EProcessError(
            "USED_EXCEEDS_TOTAL",
            f"{projection.observations_used} > {projection.observations_total}",
        )
    if projection.stopped_sequence > projection.observations_total:
        raise EProcessError("SEQUENCE_EXCEEDS_TOTAL", repr(projection.stopped_sequence))


def _check_projection_budgets(projection: EProcessProjection) -> None:
    _check_budgets(projection.max_observations, projection.max_cost)
    if (
        projection.max_observations is not None
        and projection.observations_used > projection.max_observations
    ):
        raise EProcessError("USED_EXCEEDS_MAX_OBSERVATIONS", repr(projection.observations_used))
    if projection.max_cost is not None and projection.cost_used > projection.max_cost:
        raise EProcessError("COST_EXCEEDS_MAX_COST", repr(projection.cost_used))


def _check_projection_wealth(projection: EProcessProjection) -> None:
    if projection.stop_reason == "absorbed":
        if (projection.e_value, projection.e_upper, projection.e_lower) != (0.0, 0.0, 0.0):
            raise EProcessError("ABSORBED_WITH_WEALTH", repr(projection.e_value))
        return
    if projection.e_value == 0.0:
        raise EProcessError("ZERO_WEALTH_LABELLED_MEASURED", projection.stop_reason)
    if projection.e_value != (projection.e_upper + projection.e_lower) / 2.0:
        raise EProcessError("E_VALUE_NOT_EQUAL_MIXTURE", repr(projection.e_value))
    crossed = projection.e_value >= projection.pass_e
    if projection.stop_reason == "threshold" and not crossed:
        raise EProcessError("THRESHOLD_WITHOUT_CROSSING", repr(projection.e_value))
    if projection.stop_reason != "threshold" and crossed:
        raise EProcessError("CROSSING_WITHOUT_THRESHOLD", projection.stop_reason)


def verdict(projection: EProcessProjection) -> Verdict | None:
    """pass only at the first crossing; warn otherwise; None when the stream is not measured."""
    check_projection(projection)
    if projection.stop_reason == "absorbed":
        return None
    return "pass" if projection.stop_reason == "threshold" else "warn"


def score(projection: EProcessProjection) -> float | None:
    """score, 1 - 1/max(1, e) rounded to six places; None when not measured."""
    check_projection(projection)
    if projection.stop_reason == "absorbed":
        return None
    return round(1.0 - 1.0 / max(1.0, projection.e_value), _SCORE_DIGITS)
