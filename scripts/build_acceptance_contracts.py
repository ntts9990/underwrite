"""Card: measurement-semantics | Paper: ADR-020 | Stage: semantic-repair.

Build current acceptance contracts directly from their domain owners.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

if __package__:
    from ._schema_compose import bounded_nonblank_string, object_schema, reference
else:
    from _schema_compose import bounded_nonblank_string, object_schema, reference

from underwrite.acceptance import claims, decision
from underwrite.acceptance.classify import (
    BASES,
    CLASSIFICATIONS,
    EFFECTS,
    PARTIAL_FAIL,
    REASON_CODES,
    VERDICTS,
)
from underwrite.acceptance.lifecycle import STATES
from underwrite.measurement_contract.semantics import DECISION_REASONS, MAX_IDENTITY, PROFILE

ROOT = Path(__file__).resolve().parents[1]
JSON = dict[str, Any]


def _object(properties: JSON, description: str | None = None) -> JSON:
    """Preserve the published key ordering while sharing closed-object construction."""
    body = object_schema(properties)
    return {
        "type": body["type"],
        "required": body["required"],
        "additionalProperties": body["additionalProperties"],
        **({"description": description} if description is not None else {}),
        "properties": body["properties"],
    }


def _enum(values: tuple[str, ...]) -> JSON:
    return {"type": "string", "enum": list(values)}


def _identity() -> JSON:
    return {"type": "string", "minLength": 1, "pattern": claims.ID_PATTERN}


def _digest() -> JSON:
    return {"type": "string", "pattern": claims.DIGEST_PATTERN}


def _fraction() -> JSON:
    return {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}


def _document(name: str, description: str, properties: JSON, definitions: JSON) -> JSON:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:underwrite:contract:{name}",
        "title": name,
        "description": description,
        **_object(properties),
        "$defs": definitions,
    }


def _declared_claims() -> JSON:
    claim = _object(
        {
            "claim_id": reference("id"),
            "subject_id": reference("subject_id"),
            "effect": {
                **_enum(EFFECTS),
                "description": (
                    "What a measured fail means: disqualifying makes the candidate's "
                    "classification the terminal negative one, validity leaves it indeterminate."
                ),
            },
            "policy_digest": reference("sha256_digest"),
            "semantic_digest": reference("sha256_digest"),
        }
    )
    selection = _object(
        {
            "salt": {"type": "string", "minLength": 1},
            "confirm_fraction": _fraction(),
            "case_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": claims.MAX_SELECTION_CASES,
                "description": (
                    "At most 64 reads x 2000 cases per read (measure's case bound). maxItems "
                    "and items precede uniqueItems so a malformed list is refused before "
                    "the uniqueness check."
                ),
                "items": {"type": "string", "pattern": r"\S"},
                "uniqueItems": True,
            },
        }
    )
    return _document(
        claims.SCHEMA,
        "Digest-bound claims required for each declared basis. Each claim binds an aggregate "
        "read subject and measurement-policy digest. Only the selected basis binds reads. "
        "Sealing does not prove when claims were written. The author supplies salt and "
        "confirmation fraction; selection cases assigned to confirmation are refused.",
        {
            "schema": {"const": claims.SCHEMA},
            "candidate_id": reference("id"),
            "required_claims": _object({basis: reference("basis_claims") for basis in BASES}),
            "selection": selection,
        },
        {
            "id": {
                **_identity(),
                "description": (
                    "Acceptance-owned ASCII grammar for candidate and claim identifiers."
                ),
            },
            "subject_id": bounded_nonblank_string(MAX_IDENTITY),
            "sha256_digest": _digest(),
            "basis_claims": {
                "type": "array",
                "minItems": 1,
                "maxItems": claims.MAX_CLAIMS_PER_BASIS,
                "description": (
                    "claim_id and subject_id are each unique within one basis (checked by "
                    "the claims parser; not expressible here). maxItems also bounds how many "
                    "reads one evaluation accepts."
                ),
                "items": reference("claim"),
            },
            "claim": claim,
        },
    )


def _read_summary(*, measured: bool) -> JSON:
    reasons: JSON = reference("read_reason_codes")
    if not measured:
        reasons["minItems"] = 1
    return _object(
        {
            "digest": reference("sha256_digest"),
            "verdict": _enum(tuple(value for value in VERDICTS if value != "not_measured"))
            if measured
            else {"const": "not_measured"},
            "reason_codes": reasons,
            "schema": {"const": "read.v2"},
            "profile": {"const": PROFILE},
        }
    )


def _decision_claim() -> JSON:
    return _object(
        {
            "claim_id": reference("id"),
            "subject_id": reference("subject_id"),
            "effect": _enum(EFFECTS),
            "policy_digest": reference("sha256_digest"),
            "read": {
                "description": (
                    "The bound read's summary, or null when the claim has no read "
                    "(missing_required_claim). The three shapes are disjoint (null, verdict "
                    "not_measured, measured verdict), so if/then/else selects one without "
                    "oneOf's exhaustive branch evaluation."
                ),
                "if": {"type": "null"},
                "else": {
                    "if": {"properties": {"verdict": {"const": "not_measured"}}},
                    "then": reference("not_measured_read"),
                    "else": reference("measured_read"),
                },
            },
            "semantic_digest": reference("sha256_digest"),
        }
    )


def _decision_properties() -> JSON:
    return {
        "schema": {"const": decision.SCHEMA},
        "candidate_id": reference("id"),
        "basis": _enum(BASES),
        "classification": _enum(CLASSIFICATIONS),
        "from_state": reference("state"),
        "next_state": reference("state"),
        "reason_codes": {
            "type": "array",
            "minItems": 1,
            "description": "In precedence order (decision-contract.ts ACCEPTANCE_REASON_ORDER).",
            "items": _enum(REASON_CODES),
            "uniqueItems": True,
        },
        "claims": {
            "type": "array",
            "minItems": 1,
            "maxItems": claims.MAX_CLAIMS_PER_BASIS,
            "description": "The basis's required claims, sorted by claim_id.",
            "items": reference("claim"),
        },
        "inputs": _object(
            {
                "claims_digest": reference("sha256_digest"),
                "candidate_digest": reference("sha256_digest"),
                "read_digests": {
                    "type": "array",
                    "maxItems": claims.MAX_CLAIMS_PER_BASIS,
                    "items": reference("sha256_digest"),
                    "uniqueItems": True,
                },
            },
            "Identity of what was classified. Digests establish identity, not authority, "
            "provenance or preregistration.",
        ),
        "selection": _object(
            {
                "confirm_fraction": _fraction(),
                "case_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": claims.MAX_SELECTION_CASES,
                },
            },
            "The declared split the selection cases were checked under (limitation "
            "split_parameters_author_chosen). A decision exists only when no selection case "
            "fell in confirm; how much that says depends on case_count x confirm_fraction, "
            "which a tiny author-chosen fraction makes vacuous.",
        ),
        "limitations": {
            "type": "array",
            "items": _enum((*decision.LIMITATIONS, PARTIAL_FAIL)),
            "uniqueItems": True,
            "allOf": [{"contains": {"const": item}} for item in decision.LIMITATIONS],
        },
        "human_review": {"const": "required"},
        "merge_authorized": {"const": False},
        "basis_verified": {"const": False},
    }


def _acceptance_decision() -> JSON:
    return _document(
        decision.SCHEMA,
        "One candidate's digest-bound acceptance classification and projected lifecycle. "
        "Human review is always required, merge_authorized and basis_verified stay false. "
        "Each required claim carries its supplied read summary or null when absent. "
        "Limitations disclose unchecked properties. Selection records the author-declared "
        "split. Thresholds belong to the measurement policy and reads, not this decision.",
        _decision_properties(),
        {
            "id": _identity(),
            "subject_id": bounded_nonblank_string(MAX_IDENTITY),
            "sha256_digest": _digest(),
            "state": _enum(STATES),
            "read_reason_codes": {
                "type": "array",
                "minItems": 1,
                "maxItems": len(DECISION_REASONS),
                "items": _enum(DECISION_REASONS),
                "uniqueItems": True,
                "description": "Shared read.v2 decision reasons.",
            },
            "claim": _decision_claim(),
            "measured_read": _read_summary(measured=True),
            "not_measured_read": _read_summary(measured=False),
        },
    )


def build_schemas() -> dict[str, JSON]:
    return {claims.SCHEMA: _declared_claims(), decision.SCHEMA: _acceptance_decision()}


if __name__ == "__main__":
    for name, schema in build_schemas().items():
        (ROOT / f"contracts/{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n")
