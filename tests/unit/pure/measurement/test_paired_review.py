"""Declared condition review stays bound, deterministic, and explicitly unverified."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest
from underwrite_core.canonical import content_digest

from underwrite.instrument.evidence import paired_review
from underwrite.instrument.evidence.paired_binary import evaluate_paired_binary
from underwrite.instrument.evidence.paired_review import (
    PairedReviewError,
    evaluate_paired_review,
    review_paired_evidence,
)


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


def _partition() -> dict[str, Any]:
    return {
        "taxonomy_id": "synthetic-types-v1",
        "unit": "case",
        "categories": ["z-empty", "alpha"],
        "assignments": [
            {"case_id": "a", "category": "alpha"},
            {"case_id": "b", "category": None},
        ],
    }


def _review(evidence: object, context: object) -> dict[str, Any]:
    return cast(dict[str, Any], review_paired_evidence(evidence, context))


def _evaluate(
    input_doc: object, policy_doc: object, context_doc: object
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence, review = evaluate_paired_review(input_doc, policy_doc, context_doc)
    return cast(dict[str, Any], evidence), cast(dict[str, Any], review)


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
    context_canonicalized = False

    def reject_context(value: object) -> bytes:
        nonlocal context_canonicalized
        if value is context:
            context_canonicalized = True
            raise AssertionError("context canonicalized before bounded history validation")
        return original(value)

    monkeypatch.setattr(paired_review, "canonical_bytes", reject_context)
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert context_canonicalized is False
    assert (exc.value.code, exc.value.reason) == (
        "INPUT_LIMIT_EXCEEDED",
        "EVALUATION_HISTORY_LIMIT_EXCEEDED",
    )


def test_partition_orchestrator_preserves_empty_and_unknown_groups() -> None:
    input_doc = _input()
    policy_doc = _policy()
    original = evaluate_paired_binary(input_doc, policy_doc)
    context = _context(original)
    context["case_partition"] = _partition()

    evidence, review = _evaluate(input_doc, policy_doc, context)

    assert evidence == original
    partition = review["case_partition"]
    assert partition["taxonomy_id"] == "synthetic-types-v1"
    assert partition["unit"] == "case"
    assert partition["scope"] == "primary_repeat_zero"
    groups = partition["groups"]
    assert [group["category"] for group in groups] == ["alpha", "z-empty", None]
    assert [group["case_count"] for group in groups] == [1, 0, 1]
    assert groups[0]["baseline"]["observed_zero"] == 1
    assert groups[0]["candidate"]["observed_zero"] == 1
    assert groups[0]["transitions"]["zero_to_zero"] == 1
    assert groups[1]["baseline"]["reported"] == 0
    assert groups[1]["transitions"]["complete_pair_count"] == 0
    assert groups[2]["transitions"]["zero_to_zero"] == 1
    assert review["limitations"][-1] == "CASE_PARTITION_DECLARED_NOT_VERIFIED"


def test_partition_keeps_missing_distinct_from_observed_zero() -> None:
    input_doc = _input()
    input_doc["slot_records"][1] = {
        "case_id": "a",
        "arm": "candidate",
        "repeat": 0,
        "missing_reason": "infra",
    }
    policy_doc = _policy()
    original = evaluate_paired_binary(input_doc, policy_doc)
    context = _context(original)
    context["case_partition"] = _partition()
    evidence, review = _evaluate(input_doc, policy_doc, context)
    alpha = review["case_partition"]["groups"][0]
    assert evidence["status"] == "not_measured"
    assert alpha["candidate"]["observed_zero"] == 0
    assert alpha["candidate"]["missing_by_reason"]["infra"] == 1
    assert alpha["transitions"]["complete_pair_count"] == 0
    assert alpha["transitions"]["incomplete_pair_count"] == 1


def test_empty_primary_partition_has_one_unknown_group() -> None:
    input_doc = _input()
    input_doc["primary_case_ids"] = []
    input_doc["planned_slots"] = []
    input_doc["slot_records"] = []
    policy_doc = _policy()
    original = evaluate_paired_binary(input_doc, policy_doc)
    context = _context(original)
    context["case_partition"] = {
        "taxonomy_id": "empty",
        "unit": "case",
        "categories": [],
        "assignments": [],
    }
    evidence, review = _evaluate(input_doc, policy_doc, context)
    assert evidence == original
    assert review["case_partition"]["groups"] == [
        {
            "category": None,
            "case_count": 0,
            "baseline": {
                "planned": 0,
                "reported": 0,
                "observed_one": 0,
                "observed_zero": 0,
                "missing_by_reason": {
                    "infra": 0,
                    "verifier": 0,
                    "not_started": 0,
                    "unknown": 0,
                },
                "unreported_planned": 0,
            },
            "candidate": {
                "planned": 0,
                "reported": 0,
                "observed_one": 0,
                "observed_zero": 0,
                "missing_by_reason": {
                    "infra": 0,
                    "verifier": 0,
                    "not_started": 0,
                    "unknown": 0,
                },
                "unreported_planned": 0,
            },
            "transitions": {
                "scope": "primary_repeat_zero",
                "complete_pair_count": 0,
                "incomplete_pair_count": 0,
                "zero_to_zero": 0,
                "zero_to_one": 0,
                "one_to_zero": 0,
                "one_to_one": 0,
            },
        }
    ]


def test_existing_wrapper_refuses_partition_without_original_slots() -> None:
    evidence = _evidence()
    context = _context(evidence)
    context["case_partition"] = _partition()
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == (
        "INVALID_INPUT",
        "PAIRED_INPUT_REQUIRED_FOR_PARTITION",
    )
    with pytest.raises(PairedReviewError) as exc:
        _review({}, context)
    assert (exc.value.code, exc.value.reason) == (
        "INVALID_INPUT",
        "PAIRED_INPUT_REQUIRED_FOR_PARTITION",
    )


def test_orchestrator_without_partition_matches_existing_evidence_and_review() -> None:
    input_doc = _input()
    policy_doc = _policy()
    evidence = evaluate_paired_binary(input_doc, policy_doc)
    context = _context(evidence)
    orchestrated_evidence, orchestrated_review = _evaluate(input_doc, policy_doc, context)
    assert orchestrated_evidence == evidence
    assert orchestrated_review == _review(evidence, context)


@pytest.mark.parametrize(
    ("fault", "code", "reason"),
    [
        ("duplicate_category", "INPUT_BINDING_MISMATCH", "DUPLICATE_PARTITION_CATEGORY"),
        ("duplicate_assignment", "INPUT_BINDING_MISMATCH", "DUPLICATE_PARTITION_ASSIGNMENT"),
        ("unknown_category", "INPUT_BINDING_MISMATCH", "UNKNOWN_PARTITION_CATEGORY"),
        ("missing_assignment", "INPUT_BINDING_MISMATCH", "CASE_PARTITION_BINDING_MISMATCH"),
        ("extra_assignment", "INPUT_BINDING_MISMATCH", "CASE_PARTITION_BINDING_MISMATCH"),
        ("absent_primary", "INPUT_BINDING_MISMATCH", "CASE_PARTITION_BINDING_MISMATCH"),
    ],
)
def test_partition_semantic_errors_are_typed(fault: str, code: str, reason: str) -> None:
    input_doc = _input()
    policy_doc = _policy()
    evidence = evaluate_paired_binary(input_doc, policy_doc)
    context = _context(evidence)
    partition = _partition()
    if fault == "duplicate_category":
        partition["categories"].append("alpha")
    elif fault == "duplicate_assignment":
        partition["assignments"].append({"case_id": "a", "category": None})
    elif fault == "unknown_category":
        partition["assignments"][0]["category"] = "undeclared"
    elif fault == "missing_assignment":
        partition["assignments"].pop()
    elif fault == "extra_assignment":
        partition["assignments"].append({"case_id": "extra", "category": None})
    else:
        del input_doc["primary_case_ids"]
        evidence = evaluate_paired_binary(input_doc, policy_doc)
        context = _context(evidence)
    context["case_partition"] = partition
    with pytest.raises(PairedReviewError) as exc:
        _evaluate(input_doc, policy_doc, context)
    assert (exc.value.code, exc.value.reason) == (code, reason)


def test_partition_edit_changes_context_digest_without_changing_evidence() -> None:
    input_doc = _input()
    policy_doc = _policy()
    evidence = evaluate_paired_binary(input_doc, policy_doc)
    first_context = _context(evidence)
    first_context["case_partition"] = _partition()
    first_evidence, first_review = _evaluate(input_doc, policy_doc, first_context)
    changed_context = deepcopy(first_context)
    changed_context["case_partition"]["taxonomy_id"] = "synthetic-types-v2"
    second_evidence, second_review = _evaluate(input_doc, policy_doc, changed_context)
    assert first_evidence == second_evidence
    assert first_review["context_digest"] != second_review["context_digest"]


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("categories", "CASE_PARTITION_CATEGORY_LIMIT_EXCEEDED"),
        ("assignments", "CASE_PARTITION_ASSIGNMENT_LIMIT_EXCEEDED"),
    ],
)
def test_partition_bounds_are_rejected_before_context_canonicalization(
    monkeypatch: pytest.MonkeyPatch, fault: str, reason: str
) -> None:
    evidence = _evidence()
    context = _context(evidence)
    partition = _partition()
    if fault == "categories":
        partition["categories"] = [f"category-{index}" for index in range(33)]
    else:
        partition["assignments"] = [
            {"case_id": f"case-{index}", "category": None} for index in range(129)
        ]
    context["case_partition"] = partition
    original = paired_review.canonical_bytes

    def reject_context(value: object) -> bytes:
        if value is context:
            raise AssertionError("context canonicalized before bounded partition validation")
        return original(value)

    monkeypatch.setattr(paired_review, "canonical_bytes", reject_context)
    with pytest.raises(PairedReviewError) as exc:
        _review(evidence, context)
    assert (exc.value.code, exc.value.reason) == ("INPUT_LIMIT_EXCEEDED", reason)
