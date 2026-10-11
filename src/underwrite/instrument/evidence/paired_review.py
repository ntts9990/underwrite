"""Card: paired-evidence | Paper: declared-comparison | Stage: evidence-pilot.

Review declared paired-run conditions without verifying execution or causality.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.instrument.evidence.paired_binary import (
    MAX_GROUP_CATEGORIES,
    MAX_PRIMARY_CASES,
    PairedBinaryError,
    evaluate_paired_binary,
    evaluate_paired_binary_with_groups,
)

MAX_CONTEXT_BYTES = 64 * 1024
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_CONDITIONS = 32
MAX_HISTORY_RECORDS = 32
MAX_PARTITION_CATEGORIES = MAX_GROUP_CATEGORIES
MAX_PARTITION_ASSIGNMENTS = MAX_PRIMARY_CASES
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTITY_AXES = (
    "data_digest",
    "golden_digest",
    "rubric_digest",
    "judge_id",
    "evaluator_id",
    "outcome_definition_id",
)
_EVIDENCE_REASONS = frozenset(
    {
        "PRIMARY_CASE_INVENTORY_MISSING",
        "PLANNED_SLOT_INVENTORY_MISSING",
        "PRIMARY_COHORT_INCOMPLETE",
        "INSUFFICIENT_PRIMARY_CASES",
        "ASSUMPTIONS_NOT_DECLARED",
    }
)
_BASE_LIMITATIONS = (
    "DECLARED_CONDITIONS_NOT_VERIFIED",
    "CONDITION_SCOPE_NOT_VERIFIED",
    "DIGEST_BINDING_NOT_PROVENANCE",
    "SOURCE_METADATA_AVAILABLE_IN_PAIRED_EVIDENCE",
    "NO_CAUSAL_CONCLUSION",
    "NO_APPROVAL_OR_DEPLOYMENT_AUTHORITY",
)
_HISTORY_LIMITATIONS = (
    "HISTORY_RESULT_REFERENCES_NOT_VERIFIED",
    "NO_RESCORING_PERFORMED",
    "HISTORY_COMPLETENESS_UNVERIFIED",
)
_PARTITION_LIMITATION = "CASE_PARTITION_DECLARED_NOT_VERIFIED"

ConditionRole = Literal["treatment", "invariant"]
Classification = Literal[
    "intended_change",
    "unchanged_treatment",
    "invariant_changed",
    "invariant_unchanged",
    "unknown",
]
ParsedContext = tuple[
    dict[str, object],
    list[dict[str, object]],
    dict[str, object] | None,
    dict[str, object] | None,
    bytes,
]


class PairedReviewError(ValueError):
    """A bounded error code and reason with no caller-supplied text."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


def _fail(code: str, reason: str) -> PairedReviewError:
    return PairedReviewError(code, reason)


def _object(
    value: object, *, required: frozenset[str], allowed: frozenset[str]
) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail("INVALID_INPUT", "INVALID_REVIEW_DOCUMENT")
    result = cast(dict[object, object], value)
    if any(type(key) is not str for key in result):
        raise _fail("INVALID_INPUT", "INVALID_REVIEW_DOCUMENT")
    typed = cast(dict[str, object], result)
    if not required <= typed.keys() or not typed.keys() <= allowed:
        raise _fail("INVALID_INPUT", "INVALID_REVIEW_DOCUMENT")
    return typed


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _fail("INVALID_INPUT", "INVALID_DIGEST")
    return value


def _text(value: object, maximum: int) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise _fail("INVALID_INPUT", "INVALID_CONDITION")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _fail("INVALID_INPUT", "INVALID_CONDITION") from exc
    if not unicodedata.is_normalized("NFC", value):
        raise _fail("INVALID_INPUT", "INVALID_CONDITION")
    return value


def _nullable_text(value: object) -> str | None:
    return None if value is None else _text(value, 256)


def _evidence_status(status: object, reason: object) -> tuple[str, str | None]:
    if status == "measured" and reason is None:
        return "measured", None
    if status == "not_measured" and type(reason) is str and reason in _EVIDENCE_REASONS:
        return "not_measured", reason
    raise _fail("INVALID_INPUT", "INVALID_PAIRED_EVIDENCE_REFERENCE")


def _evidence_reference(evidence: object) -> dict[str, object]:
    if type(evidence) is not dict:
        raise _fail("INVALID_INPUT", "INVALID_PAIRED_EVIDENCE_REFERENCE")
    data = cast(dict[object, object], evidence)
    if any(type(key) is not str for key in data):
        raise _fail("INVALID_INPUT", "INVALID_PAIRED_EVIDENCE_REFERENCE")
    typed = cast(dict[str, object], data)
    required = {"schema", "status", "reason", "input_digest", "policy_digest"}
    if not required <= typed.keys():
        raise _fail("INVALID_INPUT", "INVALID_PAIRED_EVIDENCE_REFERENCE")
    if typed["schema"] != "paired_binary_evidence.v1":
        raise _fail("UNSUPPORTED_PROFILE", "UNSUPPORTED_PAIRED_EVIDENCE")
    status, reason = _evidence_status(typed["status"], typed["reason"])
    input_digest = _digest(typed["input_digest"])
    policy_digest = _digest(typed["policy_digest"])
    try:
        encoded = canonical_bytes(typed)
    except (CanonicalizationError, RecursionError) as exc:
        raise _fail("INVALID_INPUT", "INVALID_PAIRED_EVIDENCE_REFERENCE") from exc
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise _fail("INPUT_LIMIT_EXCEEDED", "PAIRED_EVIDENCE_LIMIT_EXCEEDED")
    return {
        "schema": "paired_binary_evidence.v1",
        "digest": digest_bytes(encoded),
        "input_digest": input_digest,
        "policy_digest": policy_digest,
        "status": status,
        "reason": reason,
    }


def _predecessor(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail("INVALID_INPUT", "INVALID_EVALUATION_HISTORY")
    item = cast(dict[object, object], value)
    if any(type(key) is not str for key in item):
        raise _fail("INVALID_INPUT", "INVALID_EVALUATION_HISTORY")
    typed = cast(dict[str, object], item)
    relation = typed.get("relation")
    if relation in ("root", "unknown") and typed.keys() == {"relation"}:
        return {"relation": relation}
    if relation == "supersedes" and typed.keys() == {"relation", "evaluation_id"}:
        return {"relation": relation, "evaluation_id": _text(typed["evaluation_id"], 128)}
    raise _fail("INVALID_INPUT", "INVALID_EVALUATION_HISTORY")


def _identities(value: object) -> dict[str, object]:
    item = _object(
        value,
        required=frozenset(_IDENTITY_AXES),
        allowed=frozenset(_IDENTITY_AXES),
    )
    identities: dict[str, object] = {}
    for axis in _IDENTITY_AXES:
        raw = item[axis]
        if raw is None:
            identities[axis] = None
        elif axis.endswith("_digest"):
            identities[axis] = _digest(raw)
        else:
            identities[axis] = _text(raw, 128)
    return identities


def _history_record(value: object) -> dict[str, object]:
    keys = frozenset(
        {
            "evaluation_id",
            "result_digest",
            "declared_status",
            "invalidation_reason",
            "predecessor",
            "identities",
        }
    )
    item = _object(value, required=keys, allowed=keys)
    status = item["declared_status"]
    reason = item["invalidation_reason"]
    if status == "invalidated":
        if reason is None:
            raise _fail("INVALID_INPUT", "INVALID_INVALIDATION_REASON")
        normalized_reason = _text(reason, 256)
    elif status in ("active", "unknown"):
        if reason is not None:
            raise _fail("INVALID_INPUT", "INVALID_INVALIDATION_REASON")
        normalized_reason = None
    else:
        raise _fail("INVALID_INPUT", "INVALID_DECLARED_STATUS")
    return {
        "evaluation_id": _text(item["evaluation_id"], 128),
        "result_digest": _digest(item["result_digest"]),
        "declared_status": status,
        "invalidation_reason": normalized_reason,
        "predecessor": _predecessor(item["predecessor"]),
        "identities": _identities(item["identities"]),
    }


def _reject_history_cycles(records: dict[str, dict[str, object]]) -> None:
    completed: set[str] = set()
    for start in records:
        path: set[str] = set()
        current = start
        while current not in completed:
            if current in path:
                raise _fail("INVALID_INPUT", "EVALUATION_HISTORY_CYCLE")
            path.add(current)
            predecessor = cast(dict[str, object], records[current]["predecessor"])
            if predecessor["relation"] != "supersedes":
                break
            current = cast(str, predecessor["evaluation_id"])
        completed.update(path)


def _history_records(raw_records: object) -> dict[str, dict[str, object]]:
    if type(raw_records) is not list:
        raise _fail("INVALID_INPUT", "INVALID_EVALUATION_HISTORY")
    record_values = cast(list[object], raw_records)
    if len(record_values) > MAX_HISTORY_RECORDS:
        raise _fail("INPUT_LIMIT_EXCEEDED", "EVALUATION_HISTORY_LIMIT_EXCEEDED")
    records: dict[str, dict[str, object]] = {}
    for value_item in record_values:
        record = _history_record(value_item)
        evaluation_id = cast(str, record["evaluation_id"])
        if evaluation_id in records:
            raise _fail("INVALID_INPUT", "DUPLICATE_EVALUATION_ID")
        records[evaluation_id] = record
    return records


def _validate_history_links(records: dict[str, dict[str, object]]) -> None:
    for evaluation_id, record in records.items():
        predecessor = cast(dict[str, object], record["predecessor"])
        if predecessor["relation"] != "supersedes":
            continue
        predecessor_id = cast(str, predecessor["evaluation_id"])
        if predecessor_id == evaluation_id:
            raise _fail("INVALID_INPUT", "SELF_HISTORY_LINK")
        if predecessor_id not in records:
            raise _fail("INVALID_INPUT", "DANGLING_HISTORY_LINK")


def _validate_selectors(
    records: dict[str, dict[str, object]], baseline: str | None, candidate: str | None
) -> None:
    if baseline is not None and baseline not in records:
        raise _fail("INVALID_INPUT", "UNKNOWN_EVALUATION_SELECTOR")
    if candidate is not None and candidate not in records:
        raise _fail("INVALID_INPUT", "UNKNOWN_EVALUATION_SELECTOR")
    if baseline is not None and baseline == candidate:
        raise _fail("INVALID_INPUT", "DUPLICATE_EVALUATION_SELECTOR")


def _evaluation_history(value: object) -> dict[str, object]:
    keys = frozenset({"baseline_evaluation_id", "candidate_evaluation_id", "records"})
    history = _object(value, required=keys, allowed=keys)
    records = _history_records(history["records"])
    _validate_history_links(records)
    baseline = _nullable_history_id(history["baseline_evaluation_id"])
    candidate = _nullable_history_id(history["candidate_evaluation_id"])
    _validate_selectors(records, baseline, candidate)
    _reject_history_cycles(records)
    return {
        "baseline_evaluation_id": baseline,
        "candidate_evaluation_id": candidate,
        "records": [records[key] for key in sorted(records)],
    }


def _nullable_history_id(value: object) -> str | None:
    return None if value is None else _text(value, 128)


def _case_partition(value: object) -> dict[str, object]:
    keys = frozenset({"taxonomy_id", "unit", "categories", "assignments"})
    partition = _object(value, required=keys, allowed=keys)
    if partition["unit"] != "case":
        raise _fail("INVALID_INPUT", "INVALID_CASE_PARTITION")
    raw_categories = partition["categories"]
    if type(raw_categories) is not list:
        raise _fail("INVALID_INPUT", "INVALID_CASE_PARTITION")
    category_values = cast(list[object], raw_categories)
    if len(category_values) > MAX_PARTITION_CATEGORIES:
        raise _fail("INPUT_LIMIT_EXCEEDED", "CASE_PARTITION_CATEGORY_LIMIT_EXCEEDED")
    categories = [_text(category, 128) for category in category_values]
    if len(set(categories)) != len(categories):
        raise _fail("INPUT_BINDING_MISMATCH", "DUPLICATE_PARTITION_CATEGORY")
    raw_assignments = partition["assignments"]
    if type(raw_assignments) is not list:
        raise _fail("INVALID_INPUT", "INVALID_CASE_PARTITION")
    assignment_values = cast(list[object], raw_assignments)
    if len(assignment_values) > MAX_PARTITION_ASSIGNMENTS:
        raise _fail("INPUT_LIMIT_EXCEEDED", "CASE_PARTITION_ASSIGNMENT_LIMIT_EXCEEDED")
    assignments: dict[str, str | None] = {}
    for raw in assignment_values:
        assignment = _object(
            raw,
            required=frozenset({"case_id", "category"}),
            allowed=frozenset({"case_id", "category"}),
        )
        case_id = _text(assignment["case_id"], 128)
        if case_id in assignments:
            raise _fail("INPUT_BINDING_MISMATCH", "DUPLICATE_PARTITION_ASSIGNMENT")
        category = None if assignment["category"] is None else _text(assignment["category"], 128)
        if category is not None and category not in categories:
            raise _fail("INPUT_BINDING_MISMATCH", "UNKNOWN_PARTITION_CATEGORY")
        assignments[case_id] = category
    return {
        "taxonomy_id": _text(partition["taxonomy_id"], 128),
        "unit": "case",
        "categories": sorted(categories),
        "assignments": assignments,
    }


def _partition_groups(partition: dict[str, object]) -> list[dict[str, object]]:
    categories = cast(list[str], partition["categories"])
    assignments = cast(dict[str, str | None], partition["assignments"])
    return [
        {
            "category": category,
            "case_ids": sorted(
                case_id
                for case_id, assigned_category in assignments.items()
                if assigned_category == category
            ),
        }
        for category in (*categories, None)
    ]


def _context(
    context_doc: object,
) -> ParsedContext:
    context = _object(
        context_doc,
        required=frozenset({"schema", "binding", "conditions"}),
        allowed=frozenset(
            {"schema", "binding", "conditions", "evaluation_history", "case_partition"}
        ),
    )
    if context["schema"] != "paired_review_context.v1":
        raise _fail("UNSUPPORTED_PROFILE", "UNSUPPORTED_REVIEW_CONTEXT")
    binding = _object(
        context["binding"],
        required=frozenset({"input_digest", "policy_digest"}),
        allowed=frozenset({"input_digest", "policy_digest"}),
    )
    parsed_binding: dict[str, object] = {
        "input_digest": _digest(binding["input_digest"]),
        "policy_digest": _digest(binding["policy_digest"]),
    }
    if type(context["conditions"]) is not list:
        raise _fail("INVALID_INPUT", "INVALID_CONDITIONS")
    raw_conditions = cast(list[object], context["conditions"])
    if len(raw_conditions) > MAX_CONDITIONS:
        raise _fail("INPUT_LIMIT_EXCEEDED", "CONDITION_LIMIT_EXCEEDED")
    conditions: list[dict[str, object]] = []
    names: set[str] = set()
    for raw in raw_conditions:
        item = _object(
            raw,
            required=frozenset({"name", "role", "baseline", "candidate"}),
            allowed=frozenset({"name", "role", "baseline", "candidate"}),
        )
        name = _text(item["name"], 128)
        if name in names:
            raise _fail("INVALID_INPUT", "DUPLICATE_CONDITION_NAME")
        names.add(name)
        role = item["role"]
        if type(role) is not str or role not in ("treatment", "invariant"):
            raise _fail("INVALID_INPUT", "INVALID_CONDITION")
        conditions.append(
            {
                "name": name,
                "role": role,
                "baseline": _nullable_text(item["baseline"]),
                "candidate": _nullable_text(item["candidate"]),
            }
        )
    history = (
        _evaluation_history(context["evaluation_history"])
        if "evaluation_history" in context
        else None
    )
    partition = (
        _case_partition(context["case_partition"]) if "case_partition" in context else None
    )
    try:
        encoded = canonical_bytes(context_doc)
    except (CanonicalizationError, RecursionError) as exc:
        raise _fail("INVALID_INPUT", "INVALID_REVIEW_CONTEXT") from exc
    if len(encoded) > MAX_CONTEXT_BYTES:
        raise _fail("INPUT_LIMIT_EXCEEDED", "REVIEW_CONTEXT_LIMIT_EXCEEDED")
    return parsed_binding, conditions, history, partition, encoded


def _classify(condition: dict[str, object]) -> dict[str, object]:
    baseline = cast(str | None, condition["baseline"])
    candidate = cast(str | None, condition["candidate"])
    role = cast(ConditionRole, condition["role"])
    unknown_sides = [
        side
        for side, value in (("baseline", baseline), ("candidate", candidate))
        if value is None
    ]
    if unknown_sides:
        classification: Classification = "unknown"
    elif role == "treatment":
        classification = "intended_change" if baseline != candidate else "unchanged_treatment"
    else:
        classification = "invariant_changed" if baseline != candidate else "invariant_unchanged"
    return {**condition, "classification": classification, "unknown_sides": unknown_sides}


def _identity_comparison(
    records: dict[str, dict[str, object]], baseline_id: str | None, candidate_id: str | None
) -> dict[str, object]:
    baseline_identities = (
        cast(dict[str, object], records[baseline_id]["identities"])
        if baseline_id is not None
        else {}
    )
    candidate_identities = (
        cast(dict[str, object], records[candidate_id]["identities"])
        if candidate_id is not None
        else {}
    )
    comparisons: dict[str, object] = {}
    for axis in _IDENTITY_AXES:
        baseline = baseline_identities.get(axis)
        candidate = candidate_identities.get(axis)
        relation = (
            "unknown"
            if baseline is None or candidate is None
            else "same"
            if baseline == candidate
            else "different"
        )
        comparisons[axis] = {
            "relation": relation,
            "baseline": baseline,
            "candidate": candidate,
        }
    return comparisons


def _history_review(history: dict[str, object]) -> dict[str, object]:
    baseline = cast(str | None, history["baseline_evaluation_id"])
    candidate = cast(str | None, history["candidate_evaluation_id"])
    records_list = cast(list[dict[str, object]], history["records"])
    records = {cast(str, record["evaluation_id"]): record for record in records_list}
    if baseline is not None and candidate is not None:
        coverage = "complete"
    elif baseline is not None or candidate is not None:
        coverage = "partial"
    else:
        coverage = "unknown"
    return {
        "selector_coverage": coverage,
        "selectors": {
            "baseline_evaluation_id": baseline,
            "candidate_evaluation_id": candidate,
        },
        "selected_statuses": {
            "baseline": records[baseline]["declared_status"] if baseline is not None else None,
            "candidate": records[candidate]["declared_status"] if candidate is not None else None,
        },
        "identity_comparisons": _identity_comparison(records, baseline, candidate),
        "records": records_list,
    }


def _assemble_review(
    evidence: object,
    parsed_context: ParsedContext,
    partition_groups: list[dict[str, object]] | None,
) -> dict[str, object]:
    binding, conditions, history, partition, context_bytes = parsed_context
    evidence_ref = _evidence_reference(evidence)
    if any(binding[key] != evidence_ref[key] for key in ("input_digest", "policy_digest")):
        raise _fail("INPUT_BINDING_MISMATCH", "PAIRED_EVIDENCE_BINDING_MISMATCH")
    declared = bool(conditions)
    limitations: list[str] = list(_BASE_LIMITATIONS)
    if not declared:
        limitations.append("NO_CONDITIONS_DECLARED")
    if history is not None:
        limitations.extend(_HISTORY_LIMITATIONS)
    if partition is not None:
        limitations.append(_PARTITION_LIMITATION)
    result: dict[str, object] = {
        "schema": "paired_review.v1",
        "paired_evidence_ref": evidence_ref,
        "context_digest": digest_bytes(context_bytes),
        "conditions": {
            "coverage": "declared" if declared else "unknown",
            "entries": [
                _classify(item)
                for item in sorted(conditions, key=lambda item: cast(str, item["name"]))
            ],
        },
        "declarations_verified": False,
        "actual_conditions_verified": False,
        "semantic_equivalence_verified": False,
        "limitations": limitations,
    }
    if history is not None:
        result["evaluation_history"] = _history_review(history)
    if partition is not None:
        if partition_groups is None:
            raise _fail("INVALID_INPUT", "PAIRED_INPUT_REQUIRED_FOR_PARTITION")
        result["case_partition"] = {
            "taxonomy_id": partition["taxonomy_id"],
            "unit": "case",
            "scope": "primary_repeat_zero",
            "groups": partition_groups,
        }
    return result


def review_paired_evidence(evidence: object, context_doc: object) -> dict[str, object]:
    """Classify declarations bound to an independently schema-validated paired report.

    This pure boundary validates only the report fields needed for its digest reference;
    callers remain responsible for validating the complete paired evidence contract.
    A case partition additionally requires ``evaluate_paired_review`` and its original
    paired input because an evidence summary cannot reconstruct per-case slot accounting.
    """
    parsed_context = _context(context_doc)
    if parsed_context[3] is not None:
        raise _fail("INVALID_INPUT", "PAIRED_INPUT_REQUIRED_FOR_PARTITION")
    return _assemble_review(evidence, parsed_context, None)


def evaluate_paired_review(
    input_doc: object, policy_doc: object, context_doc: object
) -> tuple[dict[str, object], dict[str, object]]:
    """Evaluate paired evidence once and review its bounded declared context.

    The paired engine validates input, policy, and exact primary-case partition binding.
    Partition labels remain unverified producer declarations, and group results are
    descriptive primary repeat-zero accounting without inference or approval authority.
    """
    parsed_context = _context(context_doc)
    partition = parsed_context[3]
    partition_groups: list[dict[str, object]] | None = None
    if partition is None:
        evidence = evaluate_paired_binary(input_doc, policy_doc)
    else:
        try:
            evidence, partition_groups = evaluate_paired_binary_with_groups(
                input_doc, policy_doc, _partition_groups(partition)
            )
        except PairedBinaryError as exc:
            if exc.code == "INPUT_BINDING_MISMATCH":
                raise _fail("INPUT_BINDING_MISMATCH", "CASE_PARTITION_BINDING_MISMATCH") from exc
            raise
    review = _assemble_review(
        evidence,
        parsed_context,
        partition_groups,
    )
    return evidence, review
