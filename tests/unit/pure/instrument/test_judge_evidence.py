"""Standalone behavior and boundary checks."""

from __future__ import annotations

import itertools
import json
import math
import random
from collections.abc import Callable
from dataclasses import FrozenInstanceError, asdict, replace
from fractions import Fraction
from pathlib import Path
from typing import Literal, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes

from underwrite.instrument.evidence._resampling import ResamplingPolicy
from underwrite.instrument.evidence.judge import (
    AbstentionObservation,
    AgreementEvidence,
    JudgeCall,
    JudgeError,
    RatingEntry,
    RatingItem,
    _alpha,  # pyright: ignore[reportPrivateUsage] -- Reviewed internal formula oracle.
    _cohen_kappa,  # pyright: ignore[reportPrivateUsage] -- Not a public point-only API.
    count_abstentions,
    measure_agreement,
    reduce_ordered_pair,
)

Label = Literal["A", "B", "tie"]
BOOTSTRAP_DRAWS = 1000
MINIMUM_ITEMS = 2


@pytest.fixture
def seed() -> int:
    path = repo_root(Path(__file__).resolve()) / "contracts/statistical_seed.v1.schema.json"
    value: object = json.loads(path.read_text())["const"]
    assert type(value) is int
    return value


def _items(rows: list[tuple[Label | None, ...]]) -> list[RatingItem]:
    return [
        RatingItem(f"item-{i:02d}", tuple(RatingEntry(f"r{j}", x) for j, x in enumerate(row)))
        for i, row in enumerate(rows)
    ]


def _measure(
    items: list[RatingItem],
    seed: int,
    *,
    raters: tuple[str, str] | None = None,
    budget: int = 1000000,
) -> AgreementEvidence:
    return measure_agreement(
        items,
        judge_version="recorded-judge-v1",
        resampling=ResamplingPolicy(seed, 1000, 0.95),
        legacy_raters=raters,
        max_work=budget,
    )


def _call(order: Literal["AB", "BA"], token: str | None) -> JudgeCall | None:
    if token is None:
        return None
    if token == "error":
        return JudgeCall(order, None, "parse_failure", f"record:{order}")
    return JudgeCall(order, cast(Literal["1", "2", "tie"], token), None, f"record:{order}")


@pytest.mark.parametrize("forward,reverse", list(itertools.product(("1", "2", "tie"), repeat=2)))
def test_successful_order_matrix(forward: str, reverse: str) -> None:
    result = reduce_ordered_pair(
        item_id="case",
        judge_version="judge-v1",
        forward=_call("AB", forward),
        reverse=_call("BA", reverse),
    )
    expected = {("1", "2"): "A", ("2", "1"): "B", ("tie", "tie"): "tie"}
    assert result.preference == expected.get((forward, reverse))
    assert result.reasons == (("consistent",) if result.preference else ("position_inconsistency",))
    assert result.missing_calls == result.error_calls == 0
    assert result.normalized_labels == (
        {"1": "A", "2": "B", "tie": "tie"}[forward],
        {"1": "B", "2": "A", "tie": "tie"}[reverse],
    )
    assert result.judge_version == "judge-v1"
    assert result.item_id == "case"
    assert result.raw_calls == (_call("AB", forward), _call("BA", reverse))
    assert {
        "recorded_preference_not_superiority",
        "supplied_configuration_not_verified_execution",
    } <= set(result.limitations)


@pytest.mark.parametrize(
    "forward,reverse",
    [
        pair
        for pair in itertools.product(("1", "error", None), repeat=2)
        if "error" in pair or None in pair
    ],
)
def test_missing_and_errors_both_survive(forward: str | None, reverse: str | None) -> None:
    result = reduce_ordered_pair(
        item_id="case",
        judge_version="v1",
        forward=_call("AB", forward),
        reverse=_call("BA", reverse),
    )
    assert result.preference is None
    assert result.missing_calls == (forward is None) + (reverse is None)
    assert result.error_calls == (forward == "error") + (reverse == "error")
    assert set(result.reasons) == (
        ({"missing_call"} if None in (forward, reverse) else set())
        | ({"call_failure"} if "error" in (forward, reverse) else set())
    )
    assert result.raw_calls == (_call("AB", forward), _call("BA", reverse))


@pytest.mark.parametrize(
    "call,code",
    [
        (JudgeCall("BA", "1", None, "ref"), "INVALID_CALL_ORDER"),
        (JudgeCall("AB", "1", "error", "ref"), "INVALID_CALL_OUTCOME"),
        (JudgeCall("AB", None, None, "ref"), "INVALID_CALL_OUTCOME"),
        (JudgeCall("AB", None, "", "ref"), "INVALID_CALL_ERROR"),
        (JudgeCall("AB", "1", None, ""), "INVALID_SOURCE_REF"),
        (JudgeCall("AB", cast(Literal["1"], 1), None, "ref"), "INVALID_RESPONSE"),
        (JudgeCall("AB", cast(Literal["1"], True), None, "ref"), "INVALID_RESPONSE"),
        (JudgeCall("AB", cast(Literal["1"], "unknown"), None, "ref"), "INVALID_RESPONSE"),
    ],
)
def test_invalid_calls_fail_atomically(call: JudgeCall, code: str) -> None:
    with pytest.raises(JudgeError) as caught:
        reduce_ordered_pair(item_id="case", judge_version="v1", forward=call, reverse=None)
    assert caught.value.code == code


@pytest.mark.parametrize("version", ["", " ", "e\u0301", None, 1])
def test_version_required_and_canonical(version: object, seed: int) -> None:
    with pytest.raises(JudgeError, match="INVALID_JUDGE_VERSION"):
        reduce_ordered_pair(
            item_id="case", judge_version=cast(str, version), forward=None, reverse=None
        )
    with pytest.raises(JudgeError, match="INVALID_JUDGE_VERSION"):
        measure_agreement(
            [],
            judge_version=cast(str, version),
            resampling=ResamplingPolicy(seed, 1000, 0.95),
            legacy_raters=None,
            max_work=1,
        )


@pytest.mark.parametrize("invalid", ["\ud800", "\udfff"])
@pytest.mark.parametrize(
    "field,code",
    [
        ("ordered_item", "INVALID_ITEM_ID"),
        ("ordered_version", "INVALID_JUDGE_VERSION"),
        ("call_ref", "INVALID_SOURCE_REF"),
        ("call_error", "INVALID_CALL_ERROR"),
        ("agreement_version", "INVALID_JUDGE_VERSION"),
        ("rating_item", "INVALID_ITEM_ID"),
        ("rating_rater", "INVALID_RATER_ID"),
        ("legacy_rater", "INVALID_LEGACY_RATERS"),
        ("abstention_item", "INVALID_ITEM_ID"),
    ],
)
def test_surrogate_text_rejected_at_every_judge_boundary(
    invalid: str, field: str, code: str, seed: int
) -> None:
    actions: dict[str, Callable[[], object]] = {
        "ordered_item": lambda: reduce_ordered_pair(
            item_id=invalid, judge_version="v1", forward=None, reverse=None
        ),
        "ordered_version": lambda: reduce_ordered_pair(
            item_id="i", judge_version=invalid, forward=None, reverse=None
        ),
        "call_ref": lambda: reduce_ordered_pair(
            item_id="i",
            judge_version="v1",
            forward=JudgeCall("AB", "1", None, invalid),
            reverse=None,
        ),
        "call_error": lambda: reduce_ordered_pair(
            item_id="i",
            judge_version="v1",
            forward=JudgeCall("AB", None, invalid, "ref"),
            reverse=None,
        ),
        "agreement_version": lambda: measure_agreement(
            [],
            judge_version=invalid,
            resampling=ResamplingPolicy(seed, 1000, 0.95),
            legacy_raters=None,
            max_work=1,
        ),
        "rating_item": lambda: _measure([RatingItem(invalid, ())], seed),
        "rating_rater": lambda: _measure([RatingItem("i", (RatingEntry(invalid, "A"),))], seed),
        "legacy_rater": lambda: _measure([], seed, raters=(invalid, "r1")),
        "abstention_item": lambda: count_abstentions([AbstentionObservation(invalid, None, None)]),
    }
    with pytest.raises(JudgeError) as caught:
        actions[field]()
    assert caught.value.code == code
    assert isinstance(caught.value.__cause__, UnicodeEncodeError)


def test_supplementary_unicode_is_preserved_and_canonically_materializable(seed: int) -> None:
    identifier, version, reference = "검토-𐐀", "judge-🧪", "record://🌍"
    ordered = reduce_ordered_pair(
        item_id=identifier,
        judge_version=version,
        forward=JudgeCall("AB", None, "실패-🧪", reference),
        reverse=JudgeCall("BA", "tie", None, reference),
    )
    agreement = measure_agreement(
        [RatingItem(identifier, (RatingEntry("rater-𐐀", "A"), RatingEntry("rater-🧪", "B")))],
        judge_version=version,
        resampling=ResamplingPolicy(seed, 1000, 0.95),
        legacy_raters=("rater-𐐀", "rater-🧪"),
        max_work=1,
    )
    abstention = count_abstentions([AbstentionObservation(identifier, None, None)])
    assert (
        ordered.item_id == agreement.items[0].item_id == abstention.items[0].item_id == identifier
    )
    assert ordered.raw_calls[0] is not None and ordered.raw_calls[0].source_ref == reference
    for evidence in (ordered, agreement, abstention):
        payload = json.loads(json.dumps(asdict(evidence), allow_nan=False))
        assert json.loads(canonical_bytes(payload)) == payload


def test_internal_exact_formula_oracles() -> None:
    # Fraction-derived independently in the primary-source research; no public point-only API.
    rows: tuple[tuple[Label | None, ...], ...] = (("A", "A"), ("A", "B"), ("B", "B"))
    assert _alpha(rows) == pytest.approx(float(Fraction(4, 9)))
    assert _cohen_kappa(rows) == pytest.approx(float(Fraction(2, 5)))
    variable: tuple[tuple[Label | None, ...], ...] = (("A", "A"), ("A", "B", "B"))
    assert _alpha(variable) == pytest.approx(float(Fraction(1, 3)))
    assert _alpha((*variable, ("tie",))) == _alpha(variable)
    assert _alpha((("A", "B"),)) == 0.0
    assert _alpha(()) is None
    assert _alpha(((None,), ("A",))) is None
    assert _cohen_kappa(()) is None


@pytest.mark.parametrize(
    "rows,reason",
    [
        ([], "empty_population"),
        ([(None,), ("A",)], "no_pairable_items"),
        ([("A", "B")], "insufficient_resampling_population"),
        ([("A", "A"), ("A", "A")], "undefined_expected_disagreement"),
    ],
)
def test_no_campaign_branches(rows: list[tuple[Label | None, ...]], reason: str, seed: int) -> None:
    result = _measure(_items(rows), seed, raters=("r0", "r1"), budget=1)
    assert result.alpha.reason == reason
    assert result.legacy_cohen_kappa is not None
    assert result.legacy_cohen_kappa.reason == (
        "no_complete_selected_pairs" if reason == "no_pairable_items" else reason
    )
    for metric in (result.alpha, result.legacy_cohen_kappa):
        assert metric.availability == "not_measured"
        assert metric.value is metric.interval is None
        assert metric.input_items == len(rows)
        assert metric.bootstrap_requested == BOOTSTRAP_DRAWS
        assert metric.bootstrap_completed == metric.bootstrap_valid == 0
        assert metric.bootstrap_undefined == metric.reserved_work == 0
        assert metric.undefined_causes == ()
    assert result.reserved_work == 0


def test_real_undefined_campaign_counts_every_draw(seed: int) -> None:
    result = _measure(_items([("A", "A"), ("B", "B")]), seed, raters=("r0", "r1"))
    rng = random.Random(seed)
    valid = sum(rng.randrange(2) != rng.randrange(2) for _ in range(1000))
    for metric in (result.alpha, result.legacy_cohen_kappa):
        assert metric is not None
        assert metric.value is metric.interval is None
        assert metric.reason == "undefined_bootstrap_replicates"
        assert metric.bootstrap_completed == BOOTSTRAP_DRAWS
        assert metric.bootstrap_valid == valid
        assert metric.bootstrap_undefined == 1000 - valid
        assert [(x.reason, x.count) for x in metric.undefined_causes] == [
            ("undefined_expected_disagreement", 1000 - valid)
        ]


def _oracle_alpha(rows: list[tuple[Label | None, ...]]) -> Fraction | None:
    # Ordered-pair coincidence construction, independently of production's row-count formula.
    coincidence = {(a, b): Fraction(0) for a in ("A", "B", "tie") for b in ("A", "B", "tie")}
    for raw in rows:
        row = [x for x in raw if x is not None]
        if len(row) < MINIMUM_ITEMS:
            continue
        for i, a in enumerate(row):
            for j, b in enumerate(row):
                if i != j:
                    coincidence[a, b] += Fraction(1, len(row) - 1)
    n = sum(coincidence.values())
    if not n:
        return None
    marginals = {
        a: sum(v for (x, _), v in coincidence.items() if x == a) for a in ("A", "B", "tie")
    }
    observed = sum(v for (a, b), v in coincidence.items() if a != b)
    expected = sum(marginals[a] * marginals[b] for a in marginals for b in marginals if a != b) / (
        n - 1
    )
    return 1 - observed / expected if expected else None


def _oracle_interval(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    low = (len(ordered) - 1) * ((1.0 - 0.95) / 2.0)

    def endpoint(rank: float) -> float:
        i = math.floor(rank)
        w = Fraction(rank - i)
        return float(
            (1 - w) * Fraction(ordered[i]) + w * Fraction(ordered[min(i + 1, len(ordered) - 1)])
        )

    return endpoint(low), endpoint(float(len(ordered) - 1) - low)


def test_pairable_whole_row_bootstrap_matches_independent_coincidences(seed: int) -> None:
    rows: list[tuple[Label | None, ...]] = [("A", "B", None), ("B", None, "tie", "B"), ("A", "tie")]
    result = _measure(_items(rows), seed)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(1000):
        estimate = _oracle_alpha([rows[rng.randrange(len(rows))] for _ in rows])
        assert estimate is not None
        samples.append(float(estimate))
    expected = _oracle_alpha(rows)
    assert expected is not None
    assert result.alpha.value == pytest.approx(float(expected))
    assert result.alpha.interval == pytest.approx(_oracle_interval(samples))
    assert result.alpha.bootstrap_valid == result.alpha.bootstrap_completed == BOOTSTRAP_DRAWS
    assert result.alpha.bootstrap_undefined == 0
    assert (
        result.alpha.retained_items,
        result.alpha.retained_ratings,
        result.alpha.missing_ratings,
    ) == (3, 7, 2)
    assert result.alpha.bootstrap_method == "percentile-linear-v1"
    assert result.alpha.population == "conditional_on_retained_pairable_items"


def test_singletons_and_missing_items_do_not_change_sampling(seed: int) -> None:
    original = _items([("A", "B"), ("B", "A", None)])
    baseline = _measure(original, seed, raters=("r0", "r1"))
    expanded = [
        RatingItem("a-singleton", (RatingEntry("r0", "tie"),)),
        *reversed(original),
        RatingItem("z-missing", (RatingEntry("r0", None),)),
    ]
    after = _measure(expanded, seed, raters=("r0", "r1"))
    for before, metric in [
        (baseline.alpha, after.alpha),
        (baseline.legacy_cohen_kappa, after.legacy_cohen_kappa),
    ]:
        assert before is not None and metric is not None
        assert (
            replace(
                metric,
                input_items=before.input_items,
                excluded_items=before.excluded_items,
                exclusions=before.exclusions,
            )
            == before
        )
    assert (after.alpha.excluded_items, after.input_ratings, after.missing_ratings) == (2, 7, 2)


def test_agreement_provenance_and_conditional_population_ledger(seed: int) -> None:
    items = _items([("A", "B", None), ("B", "A", "tie"), ("A", None, "B"), ("tie",), (None, None)])
    policy = ResamplingPolicy(seed + 1, BOOTSTRAP_DRAWS + 1, 0.8)
    selected_raters = ("r0", "r1")
    budget = 1000000
    result = measure_agreement(
        list(reversed(items)),
        judge_version="supplied-reviewer-configuration-v2",
        resampling=policy,
        legacy_raters=selected_raters,
        max_work=budget,
    )
    assert result.judge_version == "supplied-reviewer-configuration-v2"
    assert result.items == tuple(items)
    assert result.legacy_raters == selected_raters
    assert result.max_work == budget
    assert result.input_ratings == sum(len(item.ratings) for item in items)
    assert result.missing_ratings == sum(
        rating.label is None for item in items for rating in item.ratings
    )
    assert result.legacy_cohen_kappa is not None
    alpha, kappa = result.alpha, result.legacy_cohen_kappa
    assert alpha.method == "krippendorff-alpha-nominal-v1"
    assert kappa.method == "cohen-kappa-two-rater-v1"
    assert alpha.population == "conditional_on_retained_pairable_items"
    assert kappa.population == "conditional_on_selected_complete_pairs"
    # The third item is pairable for alpha, but incomplete for the selected raters.
    assert (alpha.retained_items, alpha.retained_ratings, alpha.missing_ratings) == (3, 7, 2)
    assert (kappa.retained_items, kappa.retained_ratings, kappa.missing_ratings) == (2, 4, 0)
    for metric, exclusion in (
        (alpha, "fewer_than_two_observed_ratings"),
        (kappa, "incomplete_selected_pair"),
    ):
        assert metric.input_items == len(items)
        assert metric.excluded_items == len(items) - metric.retained_items
        assert [(entry.reason, entry.count) for entry in metric.exclusions] == [
            (exclusion, metric.excluded_items)
        ]
        assert metric.seed == policy.seed
        assert metric.confidence == policy.confidence
        assert metric.bootstrap_method == "percentile-linear-v1"
        assert metric.bootstrap_requested == policy.bootstrap_iterations
        assert metric.bootstrap_completed == metric.bootstrap_valid == policy.bootstrap_iterations
        assert metric.bootstrap_undefined == 0
        assert metric.undefined_causes == ()
        assert metric.availability == "measured"
        assert metric.reason is None
        assert metric.value is not None and metric.interval is not None
    assert alpha.reserved_work == policy.bootstrap_iterations * alpha.retained_items * 3
    assert kappa.reserved_work == policy.bootstrap_iterations * kappa.retained_items * 2
    assert result.reserved_work == alpha.reserved_work + kappa.reserved_work
    assert {
        "conditional_item_bootstrap_not_missingness_bias_correction",
        "nominal_agreement_not_judge_validity_or_statistical_adequacy",
        "shared_seed_replay_not_independent_statistical_evidence",
        "supplied_configuration_not_verified_execution",
        "reserved_traversal_proxy_not_executed_work_or_security_quota",
    } <= set(result.limitations)


@pytest.mark.parametrize("complete", [0, 1, 2])
def test_kappa_has_separate_population_and_stream(complete: int, seed: int) -> None:
    items = [
        RatingItem(
            f"i{i}",
            (
                RatingEntry("r0", "A"),
                RatingEntry("r1", "B" if i < complete else None),
                RatingEntry("r2", "B"),
            ),
        )
        for i in range(3)
    ]
    alpha_only = _measure(items, seed)
    combined = _measure(items, seed, raters=("r0", "r1"))
    assert alpha_only.alpha == combined.alpha
    assert alpha_only.alpha.value is not None
    kappa = combined.legacy_cohen_kappa
    assert kappa is not None and kappa.retained_items == complete
    assert kappa.excluded_items == 3 - complete
    assert kappa.method == "cohen-kappa-two-rater-v1"
    assert kappa.population == "conditional_on_selected_complete_pairs"
    assert kappa.seed == combined.alpha.seed == seed
    if complete < MINIMUM_ITEMS:
        assert kappa.value is kappa.interval is None
        assert kappa.bootstrap_completed == kappa.reserved_work == 0
    else:
        assert kappa.value == 0.0 and kappa.interval == (0.0, 0.0)
        assert kappa.bootstrap_completed == BOOTSTRAP_DRAWS


def test_widest_row_reservation_and_pre_rng_limit(
    seed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _items([("A", "B"), ("B", "A", *((None,) * 98))])
    expected = 200000 + 4000
    result = _measure(rows, seed, raters=("r0", "r1"), budget=expected)
    alpha_work, kappa_work = 200000, 4000
    assert result.alpha.reserved_work == alpha_work
    assert result.legacy_cohen_kappa is not None
    assert result.legacy_cohen_kappa.reserved_work == kappa_work
    assert result.reserved_work == result.max_work == expected

    def no_rng(*args: object, **kwargs: object) -> None:
        pytest.fail("RNG constructed before the total reservation was checked")

    monkeypatch.setattr("underwrite.instrument.evidence.judge.random.Random", no_rng)
    with pytest.raises(JudgeError) as caught:
        _measure(rows, seed, raters=("r0", "r1"), budget=expected - 1)
    assert caught.value.code == "COMPUTATION_LIMIT"
    assert f"reserved={expected}" in str(caught.value)
    assert f"max_work={expected - 1}" in str(caught.value)


@pytest.mark.parametrize(
    "ratings,code",
    [
        ((RatingEntry("r", "A"), RatingEntry("r", "B")), "DUPLICATE_RATER_ID"),
        (cast(tuple[RatingEntry, ...], [RatingEntry("r", "A")]), "INVALID_RATING_ITEM"),
        (cast(tuple[RatingEntry, ...], {"r": "A"}), "INVALID_RATING_ITEM"),
        ((RatingEntry("", "A"),), "INVALID_RATER_ID"),
        ((RatingEntry("e\u0301", "A"),), "INVALID_RATER_ID"),
        ((RatingEntry("r", cast(Label, True)),), "INVALID_RATING_LABEL"),
        ((RatingEntry("r", cast(Label, "unknown")),), "INVALID_RATING_LABEL"),
    ],
)
def test_nested_rating_validation(ratings: tuple[RatingEntry, ...], code: str, seed: int) -> None:
    with pytest.raises(JudgeError) as caught:
        _measure([RatingItem("case", ratings)], seed)
    assert caught.value.code == code


@pytest.mark.parametrize("raters", [("r0", "r0"), ("", "r1"), ("r0",), ["r0", "r1"], ("r0", 1)])
def test_invalid_selected_raters(raters: object, seed: int) -> None:
    with pytest.raises(JudgeError) as caught:
        _measure([], seed, raters=cast(tuple[str, str], raters))
    assert caught.value.code == "INVALID_LEGACY_RATERS"


@pytest.mark.parametrize("budget", [0, -1, True, 1.0, None])
def test_invalid_work_budget(budget: object, seed: int) -> None:
    with pytest.raises(JudgeError) as caught:
        _measure([], seed, budget=cast(int, budget))
    assert caught.value.code == "INVALID_MAX_WORK"


def test_duplicate_items_and_non_population_containers(seed: int) -> None:
    item = RatingItem("case", ())
    with pytest.raises(JudgeError, match="DUPLICATE_ITEM_ID"):
        _measure([item, item], seed)
    invalid_populations: tuple[object, ...] = ({}, iter([item]), "case")
    for invalid in invalid_populations:
        with pytest.raises(JudgeError, match="INVALID_POPULATION"):
            _measure(cast(list[RatingItem], invalid), seed)


def test_owned_immutable_outputs_and_failure_composition(seed: int) -> None:
    failed = reduce_ordered_pair(
        item_id="failed",
        judge_version="v1",
        forward=_call("AB", "error"),
        reverse=_call("BA", "tie"),
    )
    items = _items([("A", "B"), ("B", "A")])
    items.append(RatingItem(failed.item_id, (RatingEntry("judge", failed.preference),)))
    result = _measure(items, seed)
    original = asdict(result)
    items.clear()
    assert asdict(result) == original
    assert result.items[-1].ratings[0].label == "B"
    assert result.alpha.excluded_items == 1
    assert result.items[0].ratings[0].label is None
    with pytest.raises(FrozenInstanceError):
        result.items[0].item_id = "changed"  # type: ignore[misc]
    json.dumps(asdict(result), allow_nan=False)


def test_abstention_four_cells_and_incomplete() -> None:
    items = [
        AbstentionObservation(str(i), evidence, response)
        for i, (evidence, response) in enumerate(
            itertools.product((True, False, None), ("answer", "abstain", None))
        )
    ]
    result = count_abstentions(items)
    assert (result.total, result.incomplete) == (9, 5)
    assert (
        result.evidence_answer,
        result.evidence_abstain,
        result.no_evidence_answer,
        result.no_evidence_abstain,
    ) == (1, 1, 1, 1)
    assert result.unsupported_answer_rate == result.over_abstention_rate == float(Fraction(1, 2))
    assert "supplied_evidence_presence_not_answer_correctness" in result.limitations
    empty = count_abstentions([])
    assert empty.total == empty.incomplete == 0
    assert empty.unsupported_answer_rate is empty.over_abstention_rate is None
    items.clear()
    assert len(result.items) == len(
        tuple(itertools.product((True, False, None), ("answer", "abstain", None)))
    )


@pytest.mark.parametrize(
    "evidence,response,code",
    [
        (1, "answer", "INVALID_EVIDENCE_PRESENCE"),
        ("yes", "abstain", "INVALID_EVIDENCE_PRESENCE"),
        (False, "unknown", "INVALID_ABSTENTION_RESPONSE"),
        (True, True, "INVALID_ABSTENTION_RESPONSE"),
    ],
)
def test_invalid_abstention_labels(evidence: object, response: object, code: str) -> None:
    with pytest.raises(JudgeError) as caught:
        count_abstentions(
            [AbstentionObservation("item", cast(bool, evidence), cast(Literal["answer"], response))]
        )
    assert caught.value.code == code
    assert "item" in str(caught.value)


def test_abstention_repeated_cells_preserve_supplied_observations() -> None:
    cells: tuple[tuple[bool, Literal["answer", "abstain"]], ...] = (
        (True, "answer"),
        (True, "abstain"),
        (False, "answer"),
        (False, "abstain"),
    )
    items = [
        AbstentionObservation(f"cell-{index}-{repeat}", evidence, response)
        for index, (evidence, response) in enumerate(cells)
        for repeat in range(index + 2)
    ]
    items.extend(
        [
            AbstentionObservation("missing-evidence", None, "answer"),
            AbstentionObservation("missing-response", True, None),
        ]
    )
    result = count_abstentions(list(reversed(items)))
    expected_counts = tuple(index + 2 for index in range(len(cells)))
    assert (
        result.evidence_answer,
        result.evidence_abstain,
        result.no_evidence_answer,
        result.no_evidence_abstain,
    ) == expected_counts
    assert result.items == tuple(sorted(items, key=lambda item: item.item_id))
    assert result.incomplete == len(items) - sum(expected_counts)
    assert result.total == len(items)
    assert result.unsupported_answer_rate == float(Fraction(4, 9))
    assert result.over_abstention_rate == float(Fraction(3, 5))
    assert "supplied_evidence_presence_not_answer_correctness" in result.limitations
    items.clear()
    assert result.items[0].evidence_present is True
    assert result.items[0].response == "answer"


def test_abstention_duplicate_ids() -> None:
    item = AbstentionObservation("i", None, None)
    with pytest.raises(JudgeError, match="DUPLICATE_ITEM_ID"):
        count_abstentions([item, item])


class _TextSubclass(str):
    pass


@pytest.mark.parametrize("identifier", ["", " ", "e\u0301", 1, True, None, _TextSubclass("case")])
def test_every_item_identifier_boundary(identifier: object, seed: int) -> None:
    with pytest.raises(JudgeError, match="INVALID_ITEM_ID"):
        _measure([RatingItem(cast(str, identifier), ())], seed)
    with pytest.raises(JudgeError, match="INVALID_ITEM_ID"):
        count_abstentions([AbstentionObservation(cast(str, identifier), None, None)])
    with pytest.raises(JudgeError, match="INVALID_ITEM_ID"):
        reduce_ordered_pair(
            item_id=cast(str, identifier), judge_version="v1", forward=None, reverse=None
        )


@pytest.mark.parametrize(
    "policy,code",
    [
        (ResamplingPolicy(True, 1000, 0.95), "INVALID_SEED"),
        (ResamplingPolicy(1, 999, 0.95), "INVALID_BOOTSTRAP_ITERATIONS"),
        (ResamplingPolicy(1, 10001, 0.95), "INVALID_BOOTSTRAP_ITERATIONS"),
        (ResamplingPolicy(1, True, 0.95), "INVALID_BOOTSTRAP_ITERATIONS"),
        (ResamplingPolicy(1, 1000, 0.0), "INVALID_CONFIDENCE"),
        (ResamplingPolicy(1, 1000, 1.0), "INVALID_CONFIDENCE"),
        (ResamplingPolicy(1, 1000, float("nan")), "INVALID_CONFIDENCE"),
        (ResamplingPolicy(1, 1000, float("inf")), "INVALID_CONFIDENCE"),
        (cast(ResamplingPolicy, None), "INVALID_RESAMPLING_POLICY"),
    ],
)
def test_module_specific_resampling_errors(policy: ResamplingPolicy, code: str) -> None:
    with pytest.raises(JudgeError, match=code) as caught:
        measure_agreement([], judge_version="v1", resampling=policy, legacy_raters=None, max_work=1)
    assert caught.value.code == code


def test_record_type_and_nested_cell_types_fail_before_work(seed: int) -> None:
    with pytest.raises(JudgeError, match="INVALID_RATING_ITEM"):
        _measure(cast(list[RatingItem], [None]), seed)
    with pytest.raises(JudgeError, match="INVALID_RATING_ENTRY"):
        _measure([RatingItem("i", cast(tuple[RatingEntry, ...], (None,)))], seed)
    with pytest.raises(JudgeError, match="INVALID_ABSTENTION_ITEM"):
        count_abstentions(cast(list[AbstentionObservation], [None]))
    with pytest.raises(JudgeError, match="INVALID_POPULATION"):
        count_abstentions(cast(list[AbstentionObservation], "not a population"))
    with pytest.raises(JudgeError, match="INVALID_CALL_ORDER"):
        reduce_ordered_pair(
            item_id="i", judge_version="v1", forward=cast(JudgeCall, {}), reverse=None
        )


def test_unsupplied_calls_cannot_yield_present_ratings(seed: int) -> None:
    missing = reduce_ordered_pair(
        item_id="i", judge_version="legacy-unknown", forward=None, reverse=None
    )
    assert missing.preference is None
    item = RatingItem("i", (RatingEntry("human", "A"), RatingEntry("judge", missing.preference)))
    report = _measure([item], seed, raters=("human", "judge"))
    assert report.alpha.availability == "not_measured"
    assert report.alpha.reason == "no_pairable_items"
    assert report.legacy_cohen_kappa is not None
    assert report.legacy_cohen_kappa.reason == "no_complete_selected_pairs"
    assert report.legacy_cohen_kappa.bootstrap_completed == 0


def test_selected_pair_order_and_diagnostic_seed_are_explicit(seed: int) -> None:
    items = _items([("A", "B"), ("B", "A", "tie")])
    direct = _measure(items, seed, raters=("r0", "r1"))
    swapped = _measure(items, seed, raters=("r1", "r0"))
    assert direct.alpha == swapped.alpha
    assert direct.legacy_cohen_kappa == swapped.legacy_cohen_kappa
    assert direct.legacy_raters != swapped.legacy_raters
    diagnostic = _measure(items, seed + 1)
    assert diagnostic.alpha.seed == seed + 1
    assert direct.alpha.availability == "measured"


def test_abstention_undefined_class_denominators() -> None:
    unknown = count_abstentions([AbstentionObservation("i", None, "answer")])
    assert unknown.unsupported_answer_rate is unknown.over_abstention_rate is None
    only_evidence = count_abstentions([AbstentionObservation("i", True, "answer")])
    assert only_evidence.over_abstention_rate == 0.0
    assert only_evidence.unsupported_answer_rate is None
    only_no_evidence = count_abstentions([AbstentionObservation("i", False, "abstain")])
    assert only_no_evidence.unsupported_answer_rate == 0.0
    assert only_no_evidence.over_abstention_rate is None
