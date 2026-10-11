"""Declared condition review stays bound, deterministic, and explicitly unverified."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest
from underwrite_core.canonical import content_digest

from underwrite.instrument.evidence.paired_binary import evaluate_paired_binary
from underwrite.instrument.evidence.paired_review import PairedReviewError, review_paired_evidence


def _input() -> dict[str, Any]:
    slots = [
        {"case_id": case_id, "arm": arm, "repeat": 0}
        for case_id in ("a", "b")
        for arm in ("baseline", "candidate")
    ]
    return {
        "schema": "paired_binary_input.v1",
        "source": {"producer": "test", "version": "1", "run_id": "review"},
        "outcome_definition": {"name": "pass", "positive_means": "case passed"},
        "metadata": {
            "baseline_context_id": "base",
            "candidate_context_id": "candidate",
            "evaluator_id": "judge",
        },
        "primary_case_ids": ["a", "b"],
        "planned_slots": slots,
        "slot_records": [{**slot, "outcome": 0} for slot in slots],
    }


def _policy() -> dict[str, Any]:
    return {
        "schema": "paired_binary_policy.v1",
        "seed": 7,
        "permutation_iterations": 10_000,
        "bootstrap_iterations": 1_000,
        "confidence": 0.95,
        "max_work": 2_000_000,
        "assumptions": {
            "independent_units": True,
            "sign_flip_invariance": True,
            "fixed_sample": True,
        },
    }


def _evidence() -> dict[str, object]:
    return evaluate_paired_binary(_input(), _policy())


def _context(evidence: dict[str, object]) -> dict[str, Any]:
    return {
        "schema": "paired_review_context.v1",
        "binding": {
            "input_digest": evidence["input_digest"],
            "policy_digest": evidence["policy_digest"],
        },
        "conditions": [],
    }


def _review(evidence: object, context: object) -> dict[str, Any]:
    return cast(dict[str, Any], review_paired_evidence(evidence, context))


def test_condition_truth_table_preserves_mixed_findings_and_sorts_names() -> None:
    evidence = _evidence()
    context = _context(evidence)
    context["conditions"] = [
        {
            "name": "prompt",
            "role": "treatment",
            "baseline": "v1",
            "candidate": "v2",
        },
        {
            "name": "dataset",
            "role": "invariant",
            "baseline": "set-a",
            "candidate": "set-b",
        },
        {
            "name": "model",
            "role": "invariant",
            "baseline": "same",
            "candidate": "same",
        },
        {
            "name": "rubric",
            "role": "treatment",
            "baseline": "same",
            "candidate": "same",
        },
        {
            "name": "ocr",
            "role": "invariant",
            "baseline": None,
            "candidate": "enabled",
        },
    ]

    result = _review(evidence, context)

    assert result["conditions"]["coverage"] == "declared"
    entries = result["conditions"]["entries"]
    assert [(item["name"], item["classification"]) for item in entries] == [
        ("dataset", "invariant_changed"),
        ("model", "invariant_unchanged"),
        ("ocr", "unknown"),
        ("prompt", "intended_change"),
        ("rubric", "unchanged_treatment"),
    ]
    assert entries[2]["unknown_sides"] == ["baseline"]
    assert all(flag is False for flag in (
        result["declarations_verified"],
        result["actual_conditions_verified"],
        result["semantic_equivalence_verified"],
    ))
    assert "NO_CAUSAL_CONCLUSION" in result["limitations"]
    assert "NO_APPROVAL_OR_DEPLOYMENT_AUTHORITY" in result["limitations"]


@pytest.mark.parametrize("field", ["input_digest", "policy_digest"])
def test_both_binding_digests_are_required_to_match(field: str) -> None:
    evidence = _evidence()
    context = _context(evidence)
    context["binding"][field] = "sha256:" + "0" * 64
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == (
        "INPUT_BINDING_MISMATCH",
        "PAIRED_EVIDENCE_BINDING_MISMATCH",
    )


def test_empty_conditions_are_unknown_and_explicitly_limited() -> None:
    evidence = _evidence()
    result = _review(evidence, _context(evidence))
    assert result["conditions"] == {"coverage": "unknown", "entries": []}
    assert result["limitations"][-1] == "NO_CONDITIONS_DECLARED"


def test_null_on_both_sides_is_unknown_not_unchanged() -> None:
    evidence = _evidence()
    context = _context(evidence)
    context["conditions"] = [
        {
            "name": "runtime",
            "role": "invariant",
            "baseline": None,
            "candidate": None,
        }
    ]
    entry = _review(evidence, context)["conditions"]["entries"][0]
    assert entry["classification"] == "unknown"
    assert entry["unknown_sides"] == ["baseline", "candidate"]


def test_duplicate_condition_names_are_rejected() -> None:
    evidence = _evidence()
    context = _context(evidence)
    condition = {
        "name": "model",
        "role": "invariant",
        "baseline": "x",
        "candidate": "x",
    }
    context["conditions"] = [condition, deepcopy(condition)]
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == ("INVALID_INPUT", "DUPLICATE_CONDITION_NAME")


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("extra", "INVALID_REVIEW_DOCUMENT"),
        ("conditions_type", "INVALID_CONDITIONS"),
        ("role", "INVALID_CONDITION"),
        ("nfc", "INVALID_CONDITION"),
    ],
)
def test_context_types_shape_and_nfc_are_rejected(fault: str, reason: str) -> None:
    evidence = _evidence()
    context = _context(evidence)
    if fault == "extra":
        context["extra"] = True
    elif fault == "conditions_type":
        context["conditions"] = {}
    else:
        context["conditions"] = [
            {
                "name": "e\u0301" if fault == "nfc" else "x",
                "role": "other" if fault == "role" else "treatment",
                "baseline": "a",
                "candidate": "b",
            }
        ]
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == ("INVALID_INPUT", reason)


def test_condition_count_and_text_bounds_are_enforced() -> None:
    evidence = _evidence()
    context = _context(evidence)
    context["conditions"] = [
        {"name": f"c-{index}", "role": "invariant", "baseline": "a", "candidate": "a"}
        for index in range(33)
    ]
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == (
        "INPUT_LIMIT_EXCEEDED",
        "CONDITION_LIMIT_EXCEEDED",
    )
    context["conditions"] = [
        {"name": "x" * 129, "role": "invariant", "baseline": "a", "candidate": "a"}
    ]
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == ("INVALID_INPUT", "INVALID_CONDITION")


def test_reference_uses_exact_report_digest_status_reason_and_bindings() -> None:
    evidence = _evidence()
    evidence["status"] = "not_measured"
    evidence["reason"] = "PRIMARY_COHORT_INCOMPLETE"
    result = _review(evidence, _context(evidence))
    assert result["paired_evidence_ref"] == {
        "schema": "paired_binary_evidence.v1",
        "digest": content_digest(evidence),
        "input_digest": evidence["input_digest"],
        "policy_digest": evidence["policy_digest"],
        "status": "not_measured",
        "reason": "PRIMARY_COHORT_INCOMPLETE",
    }


def test_context_digest_is_deterministic_and_changes_with_a_declaration_edit() -> None:
    evidence = _evidence()
    context = _context(evidence)
    condition = {
        "name": "prompt",
        "role": "treatment",
        "baseline": "v1",
        "candidate": "v2",
    }
    context["conditions"] = [condition]
    first = _review(evidence, context)
    assert first["context_digest"] == content_digest(context)
    assert _review(evidence, deepcopy(context))["context_digest"] == first["context_digest"]
    changed = deepcopy(context)
    changed["conditions"][0]["candidate"] = "v3"
    assert _review(evidence, changed)["context_digest"] != first["context_digest"]
