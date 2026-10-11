"""Declared condition review stays bound, deterministic, and explicitly unverified."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest
from underwrite_core.canonical import content_digest

from underwrite.instrument.evidence import paired_review
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


def _identities(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "data_digest": "sha256:" + "1" * 64,
        "golden_digest": "sha256:" + "2" * 64,
        "rubric_digest": "sha256:" + "3" * 64,
        "judge_id": "judge-v1",
        "evaluator_id": "evaluator-v1",
        "outcome_definition_id": "binary-pass-v1",
    }
    values.update(changes)
    return values


def _history() -> dict[str, Any]:
    return {
        "baseline_evaluation_id": "old",
        "candidate_evaluation_id": "new",
        "records": [
            {
                "evaluation_id": "branch",
                "result_digest": "sha256:" + "b" * 64,
                "declared_status": "unknown",
                "invalidation_reason": None,
                "predecessor": {"relation": "supersedes", "evaluation_id": "old"},
                "identities": _identities(),
            },
            {
                "evaluation_id": "new",
                "result_digest": "sha256:" + "a" * 64,
                "declared_status": "active",
                "invalidation_reason": None,
                "predecessor": {"relation": "supersedes", "evaluation_id": "old"},
                "identities": _identities(
                    golden_digest="sha256:" + "4" * 64,
                    rubric_digest=None,
                    evaluator_id="evaluator-v2",
                ),
            },
            {
                "evaluation_id": "old",
                "result_digest": "sha256:" + "9" * 64,
                "declared_status": "invalidated",
                "invalidation_reason": "rubric corrected",
                "predecessor": {"relation": "root"},
                "identities": _identities(),
            },
        ],
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


def test_history_preserves_invalidated_predecessor_branch_and_selected_comparisons() -> None:
    evidence = _evidence()
    context = _context(evidence)
    context["evaluation_history"] = _history()

    result = _review(evidence, context)

    history = result["evaluation_history"]
    assert history["selector_coverage"] == "complete"
    assert history["selectors"] == {
        "baseline_evaluation_id": "old",
        "candidate_evaluation_id": "new",
    }
    assert history["selected_statuses"] == {"baseline": "invalidated", "candidate": "active"}
    assert [record["evaluation_id"] for record in history["records"]] == [
        "branch",
        "new",
        "old",
    ]
    assert history["records"][2]["invalidation_reason"] == "rubric corrected"
    comparisons = history["identity_comparisons"]
    assert comparisons["data_digest"]["relation"] == "same"
    assert comparisons["golden_digest"]["relation"] == "different"
    assert comparisons["rubric_digest"] == {
        "relation": "unknown",
        "baseline": "sha256:" + "3" * 64,
        "candidate": None,
    }
    assert comparisons["evaluator_id"]["relation"] == "different"
    assert result["limitations"][-3:] == [
        "HISTORY_RESULT_REFERENCES_NOT_VERIFIED",
        "NO_RESCORING_PERFORMED",
        "HISTORY_COMPLETENESS_UNVERIFIED",
    ]
    assert result["paired_evidence_ref"]["status"] == evidence["status"]
    assert result["paired_evidence_ref"]["reason"] == evidence["reason"]


@pytest.mark.parametrize(
    ("baseline", "candidate", "coverage"),
    [(None, None, "unknown"), ("old", None, "partial"), (None, "new", "partial")],
)
def test_incomplete_selectors_make_all_unavailable_comparisons_unknown(
    baseline: str | None, candidate: str | None, coverage: str
) -> None:
    evidence = _evidence()
    context = _context(evidence)
    history = _history()
    history["baseline_evaluation_id"] = baseline
    history["candidate_evaluation_id"] = candidate
    context["evaluation_history"] = history
    reviewed = _review(evidence, context)["evaluation_history"]
    assert reviewed["selector_coverage"] == coverage
    assert all(
        comparison["relation"] == "unknown"
        for comparison in reviewed["identity_comparisons"].values()
    )


def test_absent_history_preserves_slice_two_result_shape_and_limitations() -> None:
    evidence = _evidence()
    result = _review(evidence, _context(evidence))
    assert "evaluation_history" not in result
    assert result["limitations"] == [
        "DECLARED_CONDITIONS_NOT_VERIFIED",
        "CONDITION_SCOPE_NOT_VERIFIED",
        "DIGEST_BINDING_NOT_PROVENANCE",
        "SOURCE_METADATA_AVAILABLE_IN_PAIRED_EVIDENCE",
        "NO_CAUSAL_CONCLUSION",
        "NO_APPROVAL_OR_DEPLOYMENT_AUTHORITY",
        "NO_CONDITIONS_DECLARED",
    ]


def test_history_edit_changes_only_context_owned_identity() -> None:
    evidence = _evidence()
    context = _context(evidence)
    context["evaluation_history"] = _history()
    first = _review(evidence, context)
    changed = deepcopy(context)
    changed["evaluation_history"]["records"][1]["result_digest"] = "sha256:" + "c" * 64
    second = _review(evidence, changed)
    assert first["context_digest"] != second["context_digest"]
    assert first["paired_evidence_ref"] == second["paired_evidence_ref"]


def test_unknown_predecessor_is_retained_without_inventing_a_link() -> None:
    evidence = _evidence()
    context = _context(evidence)
    history = _history()
    history["records"][0]["predecessor"] = {"relation": "unknown"}
    context["evaluation_history"] = history
    records = _review(evidence, context)["evaluation_history"]["records"]
    branch = next(record for record in records if record["evaluation_id"] == "branch")
    assert branch["predecessor"] == {"relation": "unknown"}


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("duplicate", "DUPLICATE_EVALUATION_ID"),
        ("dangling", "DANGLING_HISTORY_LINK"),
        ("self", "SELF_HISTORY_LINK"),
        ("cycle", "EVALUATION_HISTORY_CYCLE"),
        ("same_selectors", "DUPLICATE_EVALUATION_SELECTOR"),
        ("unknown_selector", "UNKNOWN_EVALUATION_SELECTOR"),
    ],
)
def test_invalid_history_graphs_and_selectors_are_rejected(fault: str, reason: str) -> None:
    evidence = _evidence()
    context = _context(evidence)
    history = _history()
    if fault == "duplicate":
        history["records"].append(deepcopy(history["records"][0]))
    elif fault == "dangling":
        history["records"][0]["predecessor"]["evaluation_id"] = "missing"
    elif fault == "self":
        history["records"][0]["predecessor"]["evaluation_id"] = "branch"
    elif fault == "cycle":
        history["records"][2]["predecessor"] = {
            "relation": "supersedes",
            "evaluation_id": "new",
        }
    elif fault == "same_selectors":
        history["candidate_evaluation_id"] = "old"
    else:
        history["candidate_evaluation_id"] = "missing"
    context["evaluation_history"] = history
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == ("INVALID_INPUT", reason)


@pytest.mark.parametrize(
    ("status", "invalidation_reason"),
    [("invalidated", None), ("active", "must be null"), ("unknown", "must be null")],
)
def test_invalidation_reason_matches_declared_status(
    status: str, invalidation_reason: str | None
) -> None:
    evidence = _evidence()
    context = _context(evidence)
    history = _history()
    history["records"][0]["declared_status"] = status
    history["records"][0]["invalidation_reason"] = invalidation_reason
    context["evaluation_history"] = history
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == ("INVALID_INPUT", "INVALID_INVALIDATION_REASON")


def test_history_record_and_identity_bounds_are_enforced() -> None:
    evidence = _evidence()
    context = _context(evidence)
    history = _history()
    template = history["records"][0]
    history["records"] = [
        {
            **deepcopy(template),
            "evaluation_id": f"node-{index}",
            "predecessor": {"relation": "root"},
        }
        for index in range(33)
    ]
    history["baseline_evaluation_id"] = None
    history["candidate_evaluation_id"] = None
    context["evaluation_history"] = history
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == (
        "INPUT_LIMIT_EXCEEDED",
        "EVALUATION_HISTORY_LIMIT_EXCEEDED",
    )
    history["records"] = [deepcopy(template)]
    history["records"][0]["identities"]["data_digest"] = "not-a-digest"
    context["evaluation_history"] = history
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == ("INVALID_INPUT", "INVALID_DIGEST")


def test_history_count_is_rejected_before_context_canonicalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence()
    context = _context(evidence)
    history = _history()
    history["records"] = [deepcopy(history["records"][0]) for _ in range(33)]
    context["evaluation_history"] = history
    original = paired_review.canonical_bytes
    calls = 0

    def evidence_only(value: object) -> bytes:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("context canonicalized before bounded history validation")
        return original(value)

    monkeypatch.setattr(paired_review, "canonical_bytes", evidence_only)
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert calls == 1
    assert (exc.value.code, exc.value.reason) == (
        "INPUT_LIMIT_EXCEEDED",
        "EVALUATION_HISTORY_LIMIT_EXCEEDED",
    )
