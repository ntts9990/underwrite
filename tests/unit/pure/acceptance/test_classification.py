"""Standalone behavior and boundary checks."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from underwrite.acceptance import classify as cf
from underwrite.acceptance.classify import ClassifyInputError, classify_evidence

REQUIREMENTS: list[tuple[str, cf.Effect]] = [
    ("disqualifying-claim", "disqualifying"),
    ("validity-claim", "validity"),
]
MEASURED_AVAILABILITY: dict[str, cf.Availability] = {
    "pass": "full",
    "warn": "full",
    "fail": "full",
    "not_measured": "below_quorum",
}


def _reads(
    effect: str, read: str, *, missing: bool = False, availability: str | None = None
) -> dict[str, Any]:
    """Standalone behavior and boundary checks."""
    reads: dict[str, Any] = {}
    for claim_id, claim_effect in REQUIREMENTS:
        if claim_effect != effect:
            reads[claim_id] = ("pass", "full")
        elif not missing:
            reads[claim_id] = (read, availability or MEASURED_AVAILABILITY[read])
    return reads


def _expected(stage: str, effect: str, read: str, hard: bool) -> tuple[str, tuple[str, ...]]:
    """Standalone behavior and boundary checks."""
    if hard or (effect == "disqualifying" and read == "fail"):
        classification = "rejected"
    elif read != "pass":
        classification = "indeterminate"
    else:
        classification = "confirmed" if stage == "held_out" else "screened"
    measurement = {
        "warn": "claim_warn",
        "not_measured": "claim_not_measured",
        "fail": f"{effect}_claim_failed",
    }.get(read)
    reasons = tuple(r for r in ("hard_failure" if hard else None, measurement) if r)
    if not reasons:
        reasons = ("stage_confirmed",) if stage == "held_out" else ("stage_screened",)
    return classification, reasons


AXES = list(
    itertools.product(cf.BASES, cf.EFFECTS, ("pass", "warn", "fail", "not_measured"), (False, True))
)


@pytest.mark.parametrize(("stage", "effect", "read", "hard"), AXES)
def test_native_evidence_axes(stage: cf.Basis, effect: str, read: str, hard: bool) -> None:
    result = classify_evidence(stage, REQUIREMENTS, _reads(effect, read), hard_failure=hard)
    assert (result.classification, result.reason_codes) == _expected(stage, effect, read, hard)
    assert result.limitations == ()


@pytest.mark.parametrize(("stage", "effect"), list(itertools.product(cf.BASES, cf.EFFECTS)))
def test_missing_required_claim_is_indeterminate(stage: cf.Basis, effect: str) -> None:
    result = classify_evidence(
        stage, REQUIREMENTS, _reads(effect, "pass", missing=True), hard_failure=False
    )
    assert result.classification == "indeterminate"
    assert result.reason_codes == ("missing_required_claim",)


@pytest.mark.parametrize("read", ["warn", "not_measured"])
def test_unmeasured_and_warn_never_become_a_pass_or_a_fail(read: str) -> None:
    for stage, effect in itertools.product(cf.BASES, cf.EFFECTS):
        result = classify_evidence(stage, REQUIREMENTS, _reads(effect, read), hard_failure=False)
        assert result.classification == "indeterminate"


@pytest.mark.parametrize(
    ("effect", "classification", "reason"),
    [
        ("disqualifying", "rejected", "disqualifying_claim_failed"),
        ("validity", "indeterminate", "validity_claim_failed"),
    ],
)
def test_partial_fail_keeps_native_semantics_and_says_so(
    effect: str, classification: str, reason: str
) -> None:
    reads = _reads(effect, "fail", availability="partial")
    result = classify_evidence("held_in", REQUIREMENTS, reads, hard_failure=False)
    assert (result.classification, result.reason_codes) == (classification, (reason,))
    assert result.limitations == ("measured_fail_under_partial_availability",)


@pytest.mark.parametrize("effect", cf.EFFECTS)
def test_partial_warn_is_indeterminate_without_the_fail_limitation(effect: str) -> None:
    reads = _reads(effect, "warn", availability="partial")
    result = classify_evidence("held_out", REQUIREMENTS, reads, hard_failure=False)
    assert (result.classification, result.reason_codes) == ("indeterminate", ("claim_warn",))
    assert result.limitations == ()


def test_reasons_follow_native_precedence_whatever_the_claim_order() -> None:
    requirements: list[tuple[str, cf.Effect]] = [
        ("e-not-measured", "validity"),
        ("d-warn", "validity"),
        ("c-missing", "validity"),
        ("b-validity-fail", "validity"),
        ("a-disqualifying-fail", "disqualifying"),
    ]
    reads: dict[str, tuple[cf.Verdict, cf.Availability]] = {
        "e-not-measured": ("not_measured", "below_quorum"),
        "d-warn": ("warn", "full"),
        "b-validity-fail": ("fail", "full"),
        "a-disqualifying-fail": ("fail", "full"),
    }
    expected = cf.REASON_CODES[:6]
    for order in (requirements, requirements[::-1]):
        result = classify_evidence("same_batch", order, reads, hard_failure=True)
        assert result.classification == "rejected"
        assert result.reason_codes == expected


@pytest.mark.parametrize(
    "verdict,availability", tuple(itertools.product(cf.VERDICTS, cf.AVAILABILITY))
)
def test_classifier_admits_only_consistent_verdict_availability_pairs(
    verdict: cf.Verdict, availability: cf.Availability
) -> None:
    allowed = {
        ("pass", "full"),
        ("warn", "full"),
        ("fail", "full"),
        ("warn", "partial"),
        ("fail", "partial"),
        ("not_measured", "full"),
        ("not_measured", "partial"),
        ("not_measured", "below_quorum"),
    }
    reads: dict[str, tuple[cf.Verdict, cf.Availability]] = {"claim": (verdict, availability)}
    if (verdict, availability) not in allowed:
        with pytest.raises(ClassifyInputError) as caught:
            classify_evidence("held_out", [("claim", "validity")], reads, hard_failure=False)
        assert caught.value.code == "READ_AVAILABILITY_INCONSISTENT"
        return
    result = classify_evidence("held_out", [("claim", "validity")], reads, hard_failure=False)
    assert result.classification == ("confirmed" if verdict == "pass" else "indeterminate")
    assert (verdict, availability) in cf.CONSISTENT_READS


@pytest.mark.parametrize("state", ["full", "partial"])
@pytest.mark.parametrize("effect", cf.EFFECTS)
def test_not_measured_above_quorum_is_indeterminate(effect: str, state: str) -> None:
    reads = _reads(effect, "not_measured", availability=state)
    result = classify_evidence("held_out", REQUIREMENTS, reads, hard_failure=False)
    assert (result.classification, result.reason_codes) == (
        "indeterminate",
        ("claim_not_measured",),
    )
    assert result.limitations == ()


R = REQUIREMENTS
V = "validity-claim"
REFUSALS: list[tuple[str, Any, Any, Any, Any, str, str]] = [
    ("unknown basis", "sealed_test", R, {}, False, "INVALID_BASIS", "sealed_test"),
    ("no requirements", "held_in", [], {}, False, "INVALID_REQUIREMENT", "claim ids"),
    (
        "duplicate",
        "held_in",
        [("x", "validity")] * 2,
        {},
        False,
        "INVALID_REQUIREMENT",
        "claim ids",
    ),
    ("bad effect", "held_in", [("x", "advisory")], {}, False, "INVALID_REQUIREMENT", "x"),
    ("hard failure not bool", "held_in", R, {}, 1, "INVALID_HARD_FAILURE", "1"),
    (
        "unexpected reads",
        "held_in",
        R,
        {"b": ("pass", "full"), "a": ("pass", "full")},
        False,
        "UNEXPECTED_CLAIM",
        "a,b",
    ),
    ("unknown verdict", "held_in", R, {V: ("PASS", "full")}, False, "INVALID_READ", V),
    ("unknown state", "held_in", R, {V: ("pass", "none")}, False, "INVALID_READ", V),
    ("read not a pair", "held_in", R, {V: ("pass",)}, False, "INVALID_READ", V),
    ("read not a tuple", "held_in", R, {V: "pass"}, False, "INVALID_READ", V),
    ("read a list", "held_in", R, {V: ["pass", "full"]}, False, "INVALID_READ", V),
    (
        "partial pass",
        "held_in",
        R,
        {V: ("pass", "partial")},
        False,
        "READ_AVAILABILITY_INCONSISTENT",
        V,
    ),
    (
        "warn below quorum",
        "held_in",
        R,
        {V: ("warn", "below_quorum")},
        False,
        "READ_AVAILABILITY_INCONSISTENT",
        V,
    ),
    (
        "fail below quorum",
        "held_in",
        R,
        {V: ("fail", "below_quorum")},
        False,
        "READ_AVAILABILITY_INCONSISTENT",
        V,
    ),
]


@pytest.mark.parametrize("case", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_refuses_input_outside_the_vocabulary(
    case: tuple[str, Any, Any, Any, Any, str, str],
) -> None:
    _, basis, requirements, reads, hard, code, detail = case
    with pytest.raises(ClassifyInputError) as caught:
        classify_evidence(basis, requirements, reads, hard_failure=hard)
    assert (caught.value.code, caught.value.detail) == (code, detail)
    assert str(caught.value) == f"{code}: {detail}"
