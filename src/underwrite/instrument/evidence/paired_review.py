"""Card: paired-evidence | Paper: declared-comparison | Stage: evidence-pilot.

Review declared paired-run conditions without verifying execution or causality.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

MAX_CONTEXT_BYTES = 64 * 1024
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_CONDITIONS = 32
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
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

ConditionRole = Literal["treatment", "invariant"]
Classification = Literal[
    "intended_change",
    "unchanged_treatment",
    "invariant_changed",
    "invariant_unchanged",
    "unknown",
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


def _context(context_doc: object) -> tuple[dict[str, object], list[dict[str, object]], bytes]:
    context = _object(
        context_doc,
        required=frozenset({"schema", "binding", "conditions"}),
        allowed=frozenset({"schema", "binding", "conditions"}),
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
    try:
        encoded = canonical_bytes(context_doc)
    except (CanonicalizationError, RecursionError) as exc:
        raise _fail("INVALID_INPUT", "INVALID_REVIEW_CONTEXT") from exc
    if len(encoded) > MAX_CONTEXT_BYTES:
        raise _fail("INPUT_LIMIT_EXCEEDED", "REVIEW_CONTEXT_LIMIT_EXCEEDED")
    return parsed_binding, conditions, encoded


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


def review_paired_evidence(evidence: object, context_doc: object) -> dict[str, object]:
    """Classify declarations bound to an independently schema-validated paired report.

    This pure boundary validates only the report fields needed for its digest reference;
    callers remain responsible for validating the complete paired evidence contract.
    """
    evidence_ref = _evidence_reference(evidence)
    binding, conditions, context_bytes = _context(context_doc)
    if any(binding[key] != evidence_ref[key] for key in ("input_digest", "policy_digest")):
        raise _fail("INPUT_BINDING_MISMATCH", "PAIRED_EVIDENCE_BINDING_MISMATCH")
    declared = bool(conditions)
    limitations: list[str] = list(_BASE_LIMITATIONS)
    if not declared:
        limitations.append("NO_CONDITIONS_DECLARED")
    return {
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
