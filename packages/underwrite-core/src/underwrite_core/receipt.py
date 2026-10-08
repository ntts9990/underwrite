"""Build digest-bound execution receipts with explicit limitations.

Receipt statements preserve supplied subjects and predicates without asserting
issuer authenticity. Signature verification and missing provenance remain explicit
honesty states; building a statement does not verify external evidence."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from underwrite_core.canonical import content_digest

STATEMENT_TYPE: Final[str] = "https://in-toto.io/Statement/v1"
# Org-namespaced predicate URI kept as its own module constant so it can
# change without touching every call site.
PREDICATE_TYPE: Final[str] = "https://underwrite.dev/receipt/v1"
SIGNATURE_STATES: Final[tuple[str, ...]] = ("verified", "not_measured")
RAW_STATES: Final[tuple[str, ...]] = ("yes", "no", "retention_expired")
LEDGER_STATES: Final[tuple[str, ...]] = ("anchored", "not_measured")
HONESTY_NOTE: Final[str] = (
    "This statement does not assert issuer authenticity; signature "
    "verification is a separate, optional step."
)

_LADDER_STATES: Final[tuple[str, ...]] = ("pass", "fail", "not_measured")
_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}")


class ReceiptError(ValueError):
    """Raised when receipt.v1 statement inputs violate honesty contract.

    The message is prefixed with one of: EMPTY_SUBJECTS, MALFORMED_DIGEST,
    INVALID_PRODUCED_AT, INVALID_LIMITATION.
    """


@dataclass(frozen=True)
class Artifact:
    """One receipt subject or predicate artifact: a role name bound to a
    content digest (`sha256:<64hex>`, `underwrite_core.canonical.content_digest`)."""

    role: str
    digest: str


@dataclass(frozen=True)
class Ladder:
    """Recorded evaluator-ladder status.

    `selfcheck`/`mock_replay` are each one of `{"pass","fail","not_measured"}`;
    `aa_vacuous_rate`/`mutation_kill_rate` are `None` when not measured.
    """

    selfcheck: str
    mock_replay: str
    aa_vacuous_rate: float | None
    mutation_kill_rate: float | None


@dataclass(frozen=True)
class Limitations:
    """receipt.v1's mandatory honesty block: a not-yet-verified
    property always reads as `not_measured` (or its family's honest state),
    never as a silent pass."""

    signature_verification: str
    raw_included: str
    ledger_anchored: str
    ladder: Ladder
    notes: tuple[str, ...] = ()


def _validate_digests(subjects: Sequence[Artifact], artifacts: Sequence[Artifact]) -> None:
    for artifact in (*subjects, *artifacts):
        if _DIGEST_RE.fullmatch(artifact.digest) is None:
            raise ReceiptError(f"MALFORMED_DIGEST:{artifact.role}={artifact.digest!r}")


def _validate_produced_at(produced_at: str) -> None:
    try:
        datetime.fromisoformat(produced_at)
    except ValueError as exc:
        raise ReceiptError(f"INVALID_PRODUCED_AT:{produced_at!r}") from exc


def _validate_choice(value: str, allowed: tuple[str, ...], field_name: str) -> None:
    if value not in allowed:
        raise ReceiptError(f"INVALID_LIMITATION:{field_name}={value!r}")


def _validate_limitations(limitations: Limitations) -> None:
    _validate_choice(limitations.signature_verification, SIGNATURE_STATES, "signature_verification")
    _validate_choice(limitations.raw_included, RAW_STATES, "raw_included")
    _validate_choice(limitations.ledger_anchored, LEDGER_STATES, "ledger_anchored")
    _validate_choice(limitations.ladder.selfcheck, _LADDER_STATES, "ladder.selfcheck")
    _validate_choice(limitations.ladder.mock_replay, _LADDER_STATES, "ladder.mock_replay")


def _hex_digest(digest: str) -> str:
    """The bare hex portion of an already-validated `sha256:<hex>` digest."""
    return digest.removeprefix("sha256:")


def _subject_entry(artifact: Artifact) -> dict[str, object]:
    """in-toto ResourceDescriptor shape: `name` + `digest.sha256` (bare hex)."""
    return {"name": artifact.role, "digest": {"sha256": _hex_digest(artifact.digest)}}


def _artifact_entry(artifact: Artifact) -> dict[str, str]:
    """receipt.v1's own artifact shape: `role` + full `sha256:<hex>` digest."""
    return {"role": artifact.role, "digest": artifact.digest}


def _ladder_dict(ladder: Ladder) -> dict[str, object]:
    return {
        "selfcheck": ladder.selfcheck,
        "mock_replay": ladder.mock_replay,
        "aa_vacuous_rate": ladder.aa_vacuous_rate,
        "mutation_kill_rate": ladder.mutation_kill_rate,
    }


def _limitations_dict(limitations: Limitations) -> dict[str, object]:
    return {
        "signature_verification": limitations.signature_verification,
        "raw_included": limitations.raw_included,
        "ledger_anchored": limitations.ledger_anchored,
        "ladder": _ladder_dict(limitations.ladder),
        "notes": list(limitations.notes),
    }


def build_statement(
    *,
    subjects: Sequence[Artifact],
    predicate_type: str = PREDICATE_TYPE,
    artifacts: Sequence[Artifact],
    limitations: Limitations,
    run_id: str,
    produced_at: str,
) -> dict[str, object]:
    """Build one in-toto Statement v1 dict for an execution receipt (receipt.v1).

    `subjects` become the Statement's `subject` array (in-toto
    ResourceDescriptor shape: `name` + `digest.sha256`, bare hex); `artifacts`
    become `predicate.artifacts` (receipt.v1's own `role`/`digest` pairs, full
    `sha256:` prefix retained). `produced_at` is injected into the predicate
    verbatim -- this module never reads a wall clock.

    Raises `ReceiptError` when `subjects` is empty, any artifact digest is not
    `sha256:<64hex>`, `produced_at` is not ISO-8601, or any `limitations`
    state is outside its allowed set.
    """
    if not subjects:
        raise ReceiptError("EMPTY_SUBJECTS")
    _validate_digests(subjects, artifacts)
    _validate_produced_at(produced_at)
    _validate_limitations(limitations)

    predicate: dict[str, object] = {
        "schema": "receipt.v1",
        "run_id": run_id,
        "produced_at": produced_at,
        "artifacts": [_artifact_entry(artifact) for artifact in artifacts],
        "limitations": _limitations_dict(limitations),
        "authenticity": "not asserted",
        "honesty_note": HONESTY_NOTE,
    }
    return {
        "_type": STATEMENT_TYPE,
        "subject": [_subject_entry(artifact) for artifact in subjects],
        "predicateType": predicate_type,
        "predicate": predicate,
    }


def statement_digest(statement: dict[str, object]) -> str:
    """The receipt statement's `sha256:` content digest (`canonical.content_digest`)."""
    return content_digest(statement)
