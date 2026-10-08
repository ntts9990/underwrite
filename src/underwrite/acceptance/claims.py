"""Declared claims (``declared_claims.v1``), sealed by digest before any read is bound.
Every basis (``same_batch``/``held_in``/``held_out``) names 1..64 claims, each bound to
one read subject and to the digest of the measurement policy that holds its thresholds;
no threshold lives here. The seal is a digest over the canonical, order-normalized
document -- it binds, it does not prove when the claims were written, and the split
parameters (salt and confirm fraction) are chosen by the author, so a tiny fraction makes
the confirm check vacuous (limitation codes in ``LIMITATIONS``). The explore/confirm
assignment of the selection cases arrives as plain data from the shell, which owns the
split kernel; this module only refuses a selection that touched ``confirm``."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Final, Literal, TypeGuard

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, tagged_digest

from underwrite.acceptance.classify import BASES, EFFECTS, Basis, Effect

ClaimsErrorCode = Literal[
    "INVALID_CLAIMS",
    "MISSING_BASIS",
    "CLAIM_COUNT_OUT_OF_RANGE",
    "DUPLICATE_CLAIM_ID",
    "DUPLICATE_SUBJECT_ID",
    "INVALID_EFFECT",
    "INVALID_POLICY_DIGEST",
    "INVALID_SELECTION",
    "SELECTION_LEAKS_CONFIRM",
]

SCHEMA: Final = "declared_claims.v1"
MAX_CLAIMS_PER_BASIS: Final = 64
# One basis binds at most MAX_CLAIMS_PER_BASIS reads and a read measures at most
# measurement.read_input.MAX_CASES (2000) cases: the largest population a selection can
# be about. declared_claims.v1 case_ids maxItems; test_claims.py binds the product.
MAX_SELECTION_CASES: Final = 128_000
# read.v1 $defs.subject.subject_id pattern; test_claims.py binds this copy to its owner.
ID_PATTERN: Final = r"^[A-Za-z0-9_.:-]+$"
DIGEST_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"
DIGEST_TAG: Final = "underwrite.declared_claims.v1"
LIMITATIONS: Final = ("claims_sealing_is_digest_binding", "split_parameters_author_chosen")
_ID_RE = re.compile(ID_PATTERN)
_DIGEST_RE = re.compile(DIGEST_PATTERN)
_CLAIM_KEYS = frozenset({"claim_id", "subject_id", "effect", "policy_digest"})
_SELECTION_KEYS = frozenset({"salt", "confirm_fraction", "case_ids"})
_DOCUMENT_KEYS = frozenset({"schema", "candidate_id", "required_claims", "selection"})


class ClaimsInputError(ValueError):
    """Claims that cannot be sealed or bound; ``detail`` locates the refusal."""

    def __init__(self, code: ClaimsErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class Claim:
    """One required claim: which read subject, what a failure means, which policy."""

    claim_id: str
    subject_id: str
    effect: Effect
    policy_digest: str


@dataclass(frozen=True)
class Selection:
    """The cases the candidate was chosen on, with the split parameters to check them."""

    salt: str
    confirm_fraction: float
    case_ids: tuple[str, ...]


@dataclass(frozen=True)
class DeclaredClaims:
    """Parsed claims; each basis is sorted by ``claim_id`` and ``digest`` seals them all."""

    candidate_id: str
    same_batch: tuple[Claim, ...]
    held_in: tuple[Claim, ...]
    held_out: tuple[Claim, ...]
    selection: Selection
    digest: str


def _require(condition: bool, code: ClaimsErrorCode, detail: str) -> None:
    if not condition:
        raise ClaimsInputError(code, detail)


def _is_object(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, Mapping)


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _object(
    raw: object, keys: frozenset[str], code: ClaimsErrorCode, location: str
) -> Mapping[str, object]:
    """A decoded JSON object whose keys are a subset of ``keys`` (missing ones read as None)."""
    if not _is_object(raw) or not set(raw) <= keys:
        raise ClaimsInputError(code, location)
    return raw


def _text(value: object, pattern: re.Pattern[str], code: ClaimsErrorCode, location: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ClaimsInputError(code, location)
    return value


def _effect(value: object, location: str) -> Effect:
    for effect in EFFECTS:
        if value == effect:
            return effect
    raise ClaimsInputError("INVALID_EFFECT", location)


def _claim(raw: object, location: str) -> Claim:
    item = _object(raw, _CLAIM_KEYS, "INVALID_CLAIMS", location)
    return Claim(
        _text(item.get("claim_id"), _ID_RE, "INVALID_CLAIMS", f"{location}/claim_id"),
        _text(item.get("subject_id"), _ID_RE, "INVALID_CLAIMS", f"{location}/subject_id"),
        _effect(item.get("effect"), f"{location}/effect"),
        _text(
            item.get("policy_digest"),
            _DIGEST_RE,
            "INVALID_POLICY_DIGEST",
            f"{location}/policy_digest",
        ),
    )


def _basis(raw: object, basis: Basis) -> tuple[Claim, ...]:
    location = f"/required_claims/{basis}"
    _require(raw is not None, "MISSING_BASIS", location)
    if not _is_list(raw):
        raise ClaimsInputError("INVALID_CLAIMS", location)
    _require(0 < len(raw) <= MAX_CLAIMS_PER_BASIS, "CLAIM_COUNT_OUT_OF_RANGE", location)
    claims = [_claim(item, f"{location}/{index}") for index, item in enumerate(raw)]
    _require(len({c.claim_id for c in claims}) == len(claims), "DUPLICATE_CLAIM_ID", location)
    _require(len({c.subject_id for c in claims}) == len(claims), "DUPLICATE_SUBJECT_ID", location)
    return tuple(sorted(claims, key=lambda claim: claim.claim_id))


def _selection(raw: object) -> Selection:
    item = _object(raw, _SELECTION_KEYS, "INVALID_SELECTION", "/selection")
    salt, fraction, raw_ids = (item.get(key) for key in ("salt", "confirm_fraction", "case_ids"))
    if not isinstance(salt, str) or salt == "":
        raise ClaimsInputError("INVALID_SELECTION", "/selection/salt")
    if type(fraction) is not float or not 0 < fraction < 1:
        raise ClaimsInputError("INVALID_SELECTION", "/selection/confirm_fraction")
    if not _is_list(raw_ids) or not 0 < len(raw_ids) <= MAX_SELECTION_CASES:
        raise ClaimsInputError("INVALID_SELECTION", "/selection/case_ids")
    ids = [case_id for case_id in raw_ids if isinstance(case_id, str) and case_id.strip()]
    _require(
        len(ids) == len(raw_ids) and len(set(ids)) == len(ids),
        "INVALID_SELECTION",
        "/selection/case_ids",
    )
    return Selection(salt, fraction, tuple(sorted(ids)))


def parse_claims(document: object) -> DeclaredClaims:
    """Parse and seal one decoded ``declared_claims.v1`` document."""
    item = _object(document, _DOCUMENT_KEYS, "INVALID_CLAIMS", "/")
    _require(item.get("schema") == SCHEMA, "INVALID_CLAIMS", "/schema")
    candidate_id = _text(item.get("candidate_id"), _ID_RE, "INVALID_CLAIMS", "/candidate_id")
    required = _object(
        item.get("required_claims"), frozenset(BASES), "INVALID_CLAIMS", "/required_claims"
    )
    bases = {basis: _basis(required.get(basis), basis) for basis in BASES}
    selection = _selection(item.get("selection"))
    # The sealed body: order-normalized by construction, thresholds absent by contract.
    body = {
        "schema": SCHEMA,
        "candidate_id": candidate_id,
        "required_claims": {basis: [asdict(c) for c in claims] for basis, claims in bases.items()},
        "selection": {**asdict(selection), "case_ids": list(selection.case_ids)},
    }
    try:
        digest = tagged_digest(DIGEST_TAG, canonical_bytes(body))
    except CanonicalizationError as error:
        raise ClaimsInputError("INVALID_SELECTION", "/selection") from error
    return DeclaredClaims(
        candidate_id, bases["same_batch"], bases["held_in"], bases["held_out"], selection, digest
    )


def claims_for_basis(claims: DeclaredClaims, basis: Basis) -> tuple[Claim, ...]:
    """The claims one basis requires; only these may bind reads for that basis."""
    by_basis = {
        "same_batch": claims.same_batch,
        "held_in": claims.held_in,
        "held_out": claims.held_out,
    }
    _require(basis in by_basis, "INVALID_CLAIMS", "/basis")
    return by_basis[basis]


def check_selection(claims: DeclaredClaims, assignment: Mapping[str, str]) -> None:
    """Refuse a selection whose cases the split assigned to ``confirm``.

    ``assignment`` maps each selection case id to ``explore``/``confirm``; it must be the
    shell's split over exactly these ids with this salt and fraction, which this pure
    module cannot recompute (the split kernel is a sibling package).
    """
    _require(
        set(assignment) == set(claims.selection.case_ids)
        and set(assignment.values()) <= {"explore", "confirm"},
        "INVALID_SELECTION",
        "/selection/case_ids",
    )
    leaked = sorted(case_id for case_id, split in assignment.items() if split == "confirm")
    _require(not leaked, "SELECTION_LEAKS_CONFIRM", ",".join(leaked))
