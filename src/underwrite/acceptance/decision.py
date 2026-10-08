"""Bind declared candidate claims and reads into an acceptance decision.

Inputs are bounded and digest-checked. Missing reads remain explicit; unmeasured
and indeterminate results are never converted into passes. Declared basis and
selection are not independently verified. Every decision requires human review
and leaves merge authorization false."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal, TypeGuard, cast

from underwrite_core.canonical import CanonicalizationError, content_digest

from underwrite.acceptance import claims as cl
from underwrite.acceptance.classify import (
    AVAILABILITY,
    BASES,
    CONSISTENT_READS,
    VERDICTS,
    classify_evidence,
)
from underwrite.acceptance.lifecycle import STATES, LifecycleError, project_lifecycle

RefusalCode = Literal[
    "AUTHORITY_FIELD_REFUSED",
    "INVALID_CANDIDATE",
    "INVALID_CLAIMS",
    "INVALID_READ",
    "SELECTION_LEAKS_CONFIRM",
    "CANDIDATE_MISMATCH",
    "CLAIMS_DIGEST_MISMATCH",
    "UNEXPECTED_CLAIM",
    "DUPLICATE_CLAIM",
    "READ_POLICY_MISMATCH",
    "READ_NOT_AGGREGATE",
    "READ_AVAILABILITY_INCONSISTENT",
    "INVALID_STATE_FOR_EVALUATION",
]

SCHEMA: Final = "acceptance_decision.v1"
CANDIDATE_SCHEMA: Final = "change_candidate.v1"
KINDS: Final = ("prompt_version", "retrieval_method", "harness_patch", "model_version")
AUTHORITY_KEYS: Final = frozenset(
    {"merge_authorized", "basis_verified", "human_review", "approve", "approval", "review_action"}
)
MAX_HARD_FAILURES: Final = 64
LIMITATIONS: Final = (
    "basis_self_declared",
    *cl.LIMITATIONS,
    "reads_trusted_as_supplied",
    "read_not_bound_to_candidate",
    "hard_failures_declared",
    "escalate_not_decisive",
)
# measurement.availability.LOSS_REASON's values (a sibling pure package; bound by test).
LOSS_REASONS: Final = frozenset({"required_instrument_missing", "required_instrument_error"})
# A refusal's next action follows from which input its location points into.
REFUSAL_ACTIONS: Final = {
    "candidate": "CORRECT_CANDIDATE",
    "claims": "CORRECT_CLAIMS",
    "reads": "CORRECT_READ",
}
_CANDIDATE_KEYS = frozenset(
    {"schema", "candidate_id", "kind", "basis", "current_state", "claims_digest", "hard_failures"}
)
_PANEL_LISTS = ("required_instruments", "available", "missing")
_ID_RE = re.compile(cl.ID_PATTERN)
_DIGEST_RE = re.compile(cl.DIGEST_PATTERN)
# change_candidate.v1 hard_failures items: snake_case pattern plus maxLength 64, as one regex.
HARD_FAILURE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"
_HARD_FAILURE_RE = re.compile(HARD_FAILURE_PATTERN)


class AcceptanceRefusal(ValueError):
    """No decision. ``fields`` are the extra ``acceptance_error.v1`` keys this code carries."""

    def __init__(
        self,
        code: RefusalCode,
        location: str,
        reason: str | None = None,
        fields: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.reason = code if reason is None else reason
        self.location = location
        self.next_action = REFUSAL_ACTIONS[location.split("/")[1]]
        self.fields: Mapping[str, object] = {} if fields is None else fields
        super().__init__(f"{code}: {self.reason} at {location}")


def _require(condition: bool, code: RefusalCode, location: str, reason: str | None = None) -> None:
    if not condition:
        raise AcceptanceRefusal(code, location, reason)


def _is_object(value: object) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping)


def _is_strings(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(
        isinstance(item, str) for item in cast("list[object]", value)
    )


def authority_keys(document: object) -> tuple[str, ...]:
    """The authority field names used as object keys anywhere in ``document``, sorted.

    Iterative, so its depth is bounded by the decoder, not by the interpreter's stack.
    """
    found: set[str] = set()
    pending: list[object] = [document]
    while pending:
        value = pending.pop()
        if _is_object(value):
            found.update(key for key in value if key in AUTHORITY_KEYS)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(cast("list[object]", value))
    return tuple(sorted(found))


def _candidate(raw: object) -> Mapping[str, Any]:
    if not _is_object(raw) or frozenset(raw) != _CANDIDATE_KEYS:
        raise AcceptanceRefusal("INVALID_CANDIDATE", "/candidate")
    checks = {
        "schema": raw["schema"] == CANDIDATE_SCHEMA,
        "candidate_id": isinstance(raw["candidate_id"], str)
        and _ID_RE.fullmatch(raw["candidate_id"]) is not None,
        "kind": raw["kind"] in KINDS,
        "basis": raw["basis"] in BASES,
        "current_state": raw["current_state"] in STATES,
        "claims_digest": isinstance(raw["claims_digest"], str)
        and _DIGEST_RE.fullmatch(raw["claims_digest"]) is not None,
    }
    for field, valid in checks.items():
        _require(valid, "INVALID_CANDIDATE", f"/candidate/{field}")
    hard = raw["hard_failures"]
    _require(
        _is_strings(hard)
        and len(hard) <= MAX_HARD_FAILURES
        and all(_HARD_FAILURE_RE.fullmatch(entry) for entry in hard),
        "INVALID_CANDIDATE",
        "/candidate/hard_failures",
        "INVALID_HARD_FAILURE",
    )
    return raw


def _claims_refusal(error: cl.ClaimsInputError) -> AcceptanceRefusal:
    """Carry a claims refusal over; its detail is structural except for leaked case ids."""
    if error.code == "SELECTION_LEAKS_CONFIRM":
        return AcceptanceRefusal(error.code, "/claims/selection/case_ids")
    return AcceptanceRefusal("INVALID_CLAIMS", "/claims" + error.detail.rstrip("/"), error.code)


def _read(raw: object, where: str) -> tuple[Mapping[str, Any], str]:
    """The read (only the fields this layer uses are checked) and its canonical digest."""
    if not _is_object(raw):
        raise AcceptanceRefusal("INVALID_READ", where)
    subject, panel, sources = raw.get("subject"), raw.get("availability"), raw.get("sources")
    valid = (
        raw.get("schema") == "read.v1"
        and _is_object(subject)
        and isinstance(subject.get("subject_id"), str)
        and _is_object(subject.get("conditions"))
        and raw.get("verdict") in VERDICTS
        and type(raw.get("escalate")) is bool
        and _is_strings(raw.get("reason_codes"))
        and _is_object(sources)
        and isinstance(sources.get("policy"), str)
        and _is_object(panel)
        and panel.get("state") in AVAILABILITY
        and type(panel.get("quorum")) is int
        and type(panel.get("triggered")) is bool
        and all(_is_strings(panel.get(key)) for key in _PANEL_LISTS)
    )
    _require(valid, "INVALID_READ", where)
    try:
        return raw, content_digest(raw)
    except CanonicalizationError as error:
        raise AcceptanceRefusal("INVALID_READ", where) from error


def consistent_availability(read: Mapping[str, Any]) -> bool:
    """Whether a read's verdict, availability and loss reasons are ones a producer emits.

    The panel is one ``measurement.availability.make_policy`` admits (1 <= quorum <=
    instruments) and its state follows from the counts exactly as ``assess`` derives it;
    ``tests/unit/pure/acceptance/test_decision.py`` binds both.
    """
    panel = read["availability"]
    required, available, missing = (panel[key] for key in _PANEL_LISTS)
    reasons = set(read["reason_codes"])
    if not missing:
        state = "full"
    elif len(available) >= panel["quorum"]:
        state = "partial"
    else:
        state = "below_quorum"
    return (
        (read["verdict"], panel["state"]) in CONSISTENT_READS
        and sorted([*available, *missing]) == sorted(required) == sorted(set(required))
        and 1 <= panel["quorum"] <= len(required)
        and panel["state"] == state
        and panel["triggered"] == (state != "full")
        and (state != "partial" or read["escalate"] is True)
        and ("below_quorum" in reasons) == (state == "below_quorum")
        and (state == "full") == (not reasons & LOSS_REASONS)
    )


def _bind(
    reads: Sequence[object], basis_claims: Sequence[cl.Claim]
) -> dict[str, tuple[Mapping[str, Any], str]]:
    """Claim id -> (read, digest) for every supplied read, or the first binding refusal."""
    by_subject = {claim.subject_id: claim for claim in basis_claims}
    bound: dict[str, tuple[Mapping[str, Any], str]] = {}
    for index, raw in enumerate(reads):
        where = f"/reads/{index}"
        read, digest = _read(raw, where)
        claim = by_subject.get(read["subject"]["subject_id"])
        if claim is None:
            raise AcceptanceRefusal("UNEXPECTED_CLAIM", f"{where}/subject/subject_id")
        _require(claim.claim_id not in bound, "DUPLICATE_CLAIM", f"{where}/subject/subject_id")
        policy = read["sources"]["policy"]
        _require(policy == claim.policy_digest, "READ_POLICY_MISMATCH", f"{where}/sources/policy")
        conditions = read["subject"]["conditions"]
        _require(conditions == {}, "READ_NOT_AGGREGATE", f"{where}/subject/conditions")
        _require(
            consistent_availability(read),
            "READ_AVAILABILITY_INCONSISTENT",
            f"{where}/availability",
        )
        bound[claim.claim_id] = (read, digest)
    return bound


def _summary(claim: cl.Claim, bound: Mapping[str, tuple[Mapping[str, Any], str]]) -> dict[str, Any]:
    entry = bound.get(claim.claim_id)
    summary = None
    if entry is not None:
        read, digest = entry
        summary = {
            "digest": digest,
            "verdict": read["verdict"],
            "escalate": read["escalate"],
            "reason_codes": list(read["reason_codes"]),
        }
    return {
        "claim_id": claim.claim_id,
        "subject_id": claim.subject_id,
        "effect": claim.effect,
        "policy_digest": claim.policy_digest,
        "read": summary,
    }


def decide(
    candidate: object, claims: object, reads: Sequence[object], assignment: Mapping[str, str]
) -> dict[str, Any]:
    """The ``acceptance_decision.v1`` for one candidate, or an ``AcceptanceRefusal``.

    ``assignment`` is the shell's stable split of the claims' selection case ids.
    """
    for name, document in (("candidate", candidate), ("claims", claims)):
        _require(not authority_keys(document), "AUTHORITY_FIELD_REFUSED", f"/{name}")
    item = _candidate(candidate)
    try:
        declared = cl.parse_claims(claims)
        cl.check_selection(declared, assignment)
    except cl.ClaimsInputError as error:
        raise _claims_refusal(error) from error
    _require(
        item["candidate_id"] == declared.candidate_id,
        "CANDIDATE_MISMATCH",
        "/candidate/candidate_id",
    )
    if item["claims_digest"] != declared.digest:
        fields = {"claims_digest": declared.digest}
        raise AcceptanceRefusal("CLAIMS_DIGEST_MISMATCH", "/candidate/claims_digest", fields=fields)
    basis, current = item["basis"], item["current_state"]
    basis_claims = cl.claims_for_basis(declared, basis)
    bound = _bind(reads, basis_claims)
    result = classify_evidence(
        basis,
        [(claim.claim_id, claim.effect) for claim in basis_claims],
        {key: (read["verdict"], read["availability"]["state"]) for key, (read, _) in bound.items()},
        hard_failure=bool(item["hard_failures"]),
    )
    classification, reasons = result.classification, result.reason_codes
    try:
        following = project_lifecycle(current, classification)
    except LifecycleError as error:
        if (current, basis, classification) != ("proposed", "held_out", "confirmed"):
            context = {
                "current_state": current,
                "attempted_classification": classification,
                "reason_codes": list(reasons),
            }
            raise AcceptanceRefusal(
                "INVALID_STATE_FOR_EVALUATION",
                "/candidate/current_state",
                fields={"context": context},
            ) from error
        classification, following, reasons = "indeterminate", current, ("lifecycle_ineligible",)
    return {
        "schema": SCHEMA,
        "candidate_id": declared.candidate_id,
        "basis": basis,
        "classification": classification,
        "from_state": current,
        "next_state": following,
        "reason_codes": list(reasons),
        "claims": [_summary(claim, bound) for claim in basis_claims],
        "inputs": {
            "claims_digest": declared.digest,
            "candidate_digest": content_digest(item),
            "read_digests": sorted(digest for _, digest in bound.values()),
        },
        # How strong the confirm-leak check was: the author chose the fraction. No confirm
        # count: a decision exists only when it is 0 (SELECTION_LEAKS_CONFIRM otherwise).
        "selection": {
            "confirm_fraction": declared.selection.confirm_fraction,
            "case_count": len(declared.selection.case_ids),
        },
        "limitations": sorted({*LIMITATIONS, *result.limitations}),
        "human_review": "required",
        "merge_authorized": False,
        "basis_verified": False,
    }
