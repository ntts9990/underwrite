"""Recorded order preferences, nominal agreement, and supplied abstention counts.
Includes P20 agreement and P21 abstention; no source qualification gate.
No model execution, source verification, statistical adequacy or release authority."""

from __future__ import annotations

import random
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from underwrite.instrument.evidence._resampling import (
    ResamplingError,
    ResamplingPolicy,
    percentile_interval,
    validate_policy,
)

Label = Literal["A", "B", "tie"]
Rows = tuple[tuple[str | None, ...], ...]
MIN_PAIRABLE_RATINGS = 2
MIN_RESAMPLING_ITEMS = 2
LEGACY_RATER_COUNT = 2


class JudgeError(ValueError):
    """Atomic malformed input or an exceeded reserved traversal proxy."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class JudgeCall:
    order: Literal["AB", "BA"]
    response: Literal["1", "2", "tie"] | None
    error: str | None
    source_ref: str


@dataclass(frozen=True)
class OrderedPairEvidence:
    item_id: str
    judge_version: str
    raw_calls: tuple[JudgeCall | None, JudgeCall | None]
    normalized_labels: tuple[Label | None, Label | None]
    preference: Label | None
    reasons: tuple[str, ...]
    missing_calls: int
    error_calls: int
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class RatingEntry:
    rater_id: str
    label: Label | None


@dataclass(frozen=True)
class RatingItem:
    item_id: str
    ratings: tuple[RatingEntry, ...]


@dataclass(frozen=True)
class ReasonCount:
    reason: str
    count: int


@dataclass(frozen=True)
class AgreementMetric:
    method: str
    population: str
    availability: Literal["measured", "not_measured"]
    value: float | None
    interval: tuple[float, float] | None
    reason: str | None
    input_items: int
    retained_items: int
    excluded_items: int
    retained_ratings: int
    missing_ratings: int
    exclusions: tuple[ReasonCount, ...]
    seed: int
    confidence: float
    bootstrap_method: str
    bootstrap_requested: int
    bootstrap_completed: int
    bootstrap_valid: int
    bootstrap_undefined: int
    undefined_causes: tuple[ReasonCount, ...]
    reserved_work: int


@dataclass(frozen=True)
class AgreementEvidence:
    judge_version: str
    items: tuple[RatingItem, ...]
    input_ratings: int
    missing_ratings: int
    alpha: AgreementMetric
    legacy_raters: tuple[str, str] | None
    legacy_cohen_kappa: AgreementMetric | None
    reserved_work: int
    max_work: int
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class AbstentionObservation:
    item_id: str
    evidence_present: bool | None
    response: Literal["answer", "abstain"] | None


@dataclass(frozen=True)
class AbstentionEvidence:
    items: tuple[AbstentionObservation, ...]
    evidence_answer: int
    evidence_abstain: int
    no_evidence_answer: int
    no_evidence_abstain: int
    incomplete: int
    limitations: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def unsupported_answer_rate(self) -> float | None:
        return _unsupported_answer_rate(self)

    @property
    def over_abstention_rate(self) -> float | None:
        return _over_abstention_rate(self)


def _share(numerator: int, other: int) -> float | None:
    """Return the numerator's share of its arm, or None for an empty arm.

    An empty population has no rate; it never becomes a zero abstention rate."""
    total = numerator + other
    return numerator / total if total else None


def _unsupported_answer_rate(evidence: AbstentionEvidence) -> float | None:
    return _share(evidence.no_evidence_answer, evidence.no_evidence_abstain)


def _over_abstention_rate(evidence: AbstentionEvidence) -> float | None:
    return _share(evidence.evidence_abstain, evidence.evidence_answer)


def _text(value: str, code: str) -> None:
    if type(value) is not str or not value.strip() or unicodedata.normalize("NFC", value) != value:
        raise JudgeError(code)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise JudgeError(code) from exc


def _unique(value: str, seen: set[str], kind: str) -> None:
    _text(value, f"INVALID_{kind}_ID")
    if value in seen:
        raise JudgeError(f"DUPLICATE_{kind}_ID", value)
    seen.add(value)


def _call(call: JudgeCall | None, order: Literal["AB", "BA"]) -> JudgeCall | None:
    if call is None:
        return None
    if type(call) is not JudgeCall or type(call.order) is not str or call.order != order:
        raise JudgeError("INVALID_CALL_ORDER")
    _text(call.source_ref, "INVALID_SOURCE_REF")
    if (call.response is None) == (call.error is None):
        raise JudgeError("INVALID_CALL_OUTCOME")
    if call.error is not None:
        _text(call.error, "INVALID_CALL_ERROR")
    elif type(call.response) is not str or call.response not in ("1", "2", "tie"):
        raise JudgeError("INVALID_RESPONSE")
    return JudgeCall(call.order, call.response, call.error, call.source_ref)


def _label(call: JudgeCall | None) -> Label | None:
    if call is None or call.error is not None:
        return None
    if call.response == "tie":
        return "tie"
    return "A" if (call.order == "AB") == (call.response == "1") else "B"


def reduce_ordered_pair(
    *,
    item_id: str,
    judge_version: str,
    forward: JudgeCall | None,
    reverse: JudgeCall | None,
) -> OrderedPairEvidence:
    """Normalize recorded AB/BA calls; inconsistent preferences are not ties."""
    _text(item_id, "INVALID_ITEM_ID")
    _text(judge_version, "INVALID_JUDGE_VERSION")
    calls = (_call(forward, "AB"), _call(reverse, "BA"))
    labels = (_label(calls[0]), _label(calls[1]))
    missing = sum(call is None for call in calls)
    errors = sum(call is not None and call.error is not None for call in calls)
    reasons = tuple(
        reason
        for present, reason in (
            (missing, "missing_call"),
            (errors, "call_failure"),
        )
        if present
    )
    preference = None
    if not reasons:
        preference = labels[0] if labels[0] == labels[1] else None
        reasons = ("consistent",) if preference is not None else ("position_inconsistency",)
    return OrderedPairEvidence(
        item_id,
        judge_version,
        calls,
        labels,
        preference,
        reasons,
        missing,
        errors,
        ("recorded_preference_not_superiority", "supplied_configuration_not_verified_execution"),
    )


def _rating_items(items: Sequence[RatingItem]) -> tuple[RatingItem, ...]:
    if type(items) not in (list, tuple):
        raise JudgeError("INVALID_POPULATION")
    seen: set[str] = set()
    copied: list[RatingItem] = []
    for item in items:
        if type(item) is not RatingItem or type(item.ratings) is not tuple:
            raise JudgeError("INVALID_RATING_ITEM")
        _unique(item.item_id, seen, "ITEM")
        raters: set[str] = set()
        ratings: list[RatingEntry] = []
        for rating in item.ratings:
            if type(rating) is not RatingEntry:
                raise JudgeError("INVALID_RATING_ENTRY")
            _unique(rating.rater_id, raters, "RATER")
            if rating.label is not None and (
                type(rating.label) is not str or rating.label not in ("A", "B", "tie")
            ):
                raise JudgeError("INVALID_RATING_LABEL", item.item_id)
            ratings.append(RatingEntry(rating.rater_id, rating.label))
        copied.append(RatingItem(item.item_id, tuple(sorted(ratings, key=lambda r: r.rater_id))))
    return tuple(sorted(copied, key=lambda item: item.item_id))


def _alpha(rows: Rows) -> float | None:
    """Internal nominal scalar: omit singleton rows from BOTH disagreement terms."""
    pooled: Counter[str] = Counter()
    disagreement = Fraction(0)
    for row in rows:
        counts = Counter(label for label in row if label is not None)
        n = sum(counts.values())
        if n < MIN_PAIRABLE_RATINGS:
            continue
        pooled.update(counts)
        disagreement += Fraction(n * n - sum(x * x for x in counts.values()), n - 1)
    n = sum(pooled.values())
    denominator = n * n - sum(x * x for x in pooled.values())
    return float(1 - disagreement * (n - 1) / denominator) if denominator else None


def _cohen_kappa(rows: Rows) -> float | None:
    """Internal two-complete-rater scalar with separate annotator marginals."""
    n = len(rows)
    first = Counter(row[0] for row in rows)
    second = Counter(row[1] for row in rows)
    expected = sum(count * second[label] for label, count in first.items())
    denominator = n * n - expected
    matches = sum(row[0] == row[1] for row in rows)
    return float(Fraction(n * matches - expected, denominator)) if denominator else None


def _reservation(rows: Rows, point: float | None, iterations: int) -> int:
    if len(rows) < MIN_RESAMPLING_ITEMS or point is None:
        return 0
    return iterations * len(rows) * max(map(len, rows))


def _metric(
    rows: Rows,
    point: float | None,
    *,
    legacy: bool,
    input_items: int,
    policy: ResamplingPolicy,
    reservation: int,
) -> AgreementMetric:
    reason = None
    if not rows:
        reason = (
            "empty_population"
            if not input_items
            else ("no_complete_selected_pairs" if legacy else "no_pairable_items")
        )
    elif len(rows) < MIN_RESAMPLING_ITEMS:
        reason = "insufficient_resampling_population"
    elif point is None:
        reason = "undefined_expected_disagreement"
    samples: list[float] = []
    undefined = 0
    interval = None
    if reason is None:
        rng = random.Random(policy.seed)
        for _ in range(policy.bootstrap_iterations):
            draw = tuple(rows[rng.randrange(len(rows))] for _ in rows)
            value = _cohen_kappa(draw) if legacy else _alpha(draw)
            if value is None:
                undefined += 1
            else:
                samples.append(value)
        if undefined:
            reason = "undefined_bootstrap_replicates"
        else:
            interval = percentile_interval(samples, confidence=policy.confidence)
    excluded = input_items - len(rows)
    return AgreementMetric(
        method="cohen-kappa-two-rater-v1" if legacy else "krippendorff-alpha-nominal-v1",
        availability="measured" if reason is None else "not_measured",
        population="conditional_on_selected_complete_pairs"
        if legacy
        else "conditional_on_retained_pairable_items",
        value=point if reason is None else None,
        interval=interval,
        reason=reason,
        input_items=input_items,
        retained_items=len(rows),
        excluded_items=excluded,
        retained_ratings=sum(label is not None for row in rows for label in row),
        missing_ratings=sum(label is None for row in rows for label in row),
        exclusions=(
            ReasonCount(
                "incomplete_selected_pair" if legacy else "fewer_than_two_observed_ratings",
                excluded,
            ),
        )
        if excluded
        else (),
        seed=policy.seed,
        confidence=policy.confidence,
        bootstrap_method="percentile-linear-v1",
        bootstrap_requested=policy.bootstrap_iterations,
        bootstrap_completed=len(samples) + undefined,
        bootstrap_valid=len(samples),
        bootstrap_undefined=undefined,
        undefined_causes=(ReasonCount("undefined_expected_disagreement", undefined),)
        if undefined
        else (),
        reserved_work=reservation,
    )


def _legacy_pair(raters: tuple[str, str] | None) -> tuple[str, str] | None:
    if raters is None:
        return None
    if type(raters) is not tuple or len(raters) != LEGACY_RATER_COUNT:
        raise JudgeError("INVALID_LEGACY_RATERS")
    for rater in raters:
        _text(rater, "INVALID_LEGACY_RATERS")
    if raters[0] == raters[1]:
        raise JudgeError("INVALID_LEGACY_RATERS")
    return raters[0], raters[1]


def measure_agreement(
    items: Sequence[RatingItem],
    *,
    judge_version: str,
    resampling: ResamplingPolicy,
    legacy_raters: tuple[str, str] | None,
    max_work: int,
) -> AgreementEvidence:
    """Report each statistic with its own CI, or neither when any draw is undefined.

    Bootstrap populations are conditional on retained rows, not a missingness-bias
    correction. Reserved work bounds resampled-cell traversal, excluding validation,
    sorting, allocations, point estimation and numeric bit cost; it is not a CPU quota.
    """
    _text(judge_version, "INVALID_JUDGE_VERSION")
    try:
        validate_policy(resampling)
    except ResamplingError as exc:
        raise JudgeError(exc.code) from exc
    if type(max_work) is not int or max_work <= 0:
        raise JudgeError("INVALID_MAX_WORK")
    raters = _legacy_pair(legacy_raters)
    copied = _rating_items(items)
    all_rows = tuple(tuple(r.label for r in item.ratings) for item in copied)
    rows = tuple(row for row in all_rows if sum(x is not None for x in row) >= MIN_PAIRABLE_RATINGS)
    point = _alpha(rows)
    reservation = _reservation(rows, point, resampling.bootstrap_iterations)
    selected: list[tuple[str | None, ...]] = []
    if raters is not None:
        for item in copied:
            labels = {r.rater_id: r.label for r in item.ratings}
            a, b = labels.get(raters[0]), labels.get(raters[1])
            if a is not None and b is not None:
                selected.append((a, b))
    kappa_rows = tuple(selected)
    kappa_point = _cohen_kappa(kappa_rows)
    kappa_reservation = _reservation(kappa_rows, kappa_point, resampling.bootstrap_iterations)
    total_work = reservation + kappa_reservation
    if total_work > max_work:
        raise JudgeError("COMPUTATION_LIMIT", f"reserved={total_work}, max_work={max_work}")
    alpha = _metric(
        rows,
        point,
        legacy=False,
        input_items=len(copied),
        policy=resampling,
        reservation=reservation,
    )
    kappa = (
        None
        if raters is None
        else _metric(
            kappa_rows,
            kappa_point,
            legacy=True,
            input_items=len(copied),
            policy=resampling,
            reservation=kappa_reservation,
        )
    )
    return AgreementEvidence(
        judge_version,
        copied,
        sum(map(len, all_rows)),
        sum(label is None for row in all_rows for label in row),
        alpha,
        raters,
        kappa,
        total_work,
        max_work,
        (
            "conditional_item_bootstrap_not_missingness_bias_correction",
            "nominal_agreement_not_judge_validity_or_statistical_adequacy",
            "shared_seed_replay_not_independent_statistical_evidence",
            "supplied_configuration_not_verified_execution",
            "reserved_traversal_proxy_not_executed_work_or_security_quota",
        ),
    )


def count_abstentions(items: Sequence[AbstentionObservation]) -> AbstentionEvidence:
    """Count the supplied evidence/response table, without inferring correctness."""
    if type(items) not in (list, tuple):
        raise JudgeError("INVALID_POPULATION")
    seen: set[str] = set()
    copied: list[AbstentionObservation] = []
    cells: Counter[tuple[bool, str]] = Counter()
    incomplete = 0
    for item in items:
        if type(item) is not AbstentionObservation:
            raise JudgeError("INVALID_ABSTENTION_ITEM")
        _unique(item.item_id, seen, "ITEM")
        if item.evidence_present is not None and type(item.evidence_present) is not bool:
            raise JudgeError("INVALID_EVIDENCE_PRESENCE", item.item_id)
        if item.response is not None and (
            type(item.response) is not str or item.response not in ("answer", "abstain")
        ):
            raise JudgeError("INVALID_ABSTENTION_RESPONSE", item.item_id)
        if item.evidence_present is None or item.response is None:
            incomplete += 1
        else:
            cells[item.evidence_present, item.response] += 1
        copied.append(AbstentionObservation(item.item_id, item.evidence_present, item.response))
    return AbstentionEvidence(
        tuple(sorted(copied, key=lambda item: item.item_id)),
        cells[True, "answer"],
        cells[True, "abstain"],
        cells[False, "answer"],
        cells[False, "abstain"],
        incomplete,
        ("supplied_evidence_presence_not_answer_correctness",),
    )
