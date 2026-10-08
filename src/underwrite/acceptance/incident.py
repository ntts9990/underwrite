"""Incident-to-Eval at library level: an ``observation.v1`` of kind ``incident_record``
(payload ``input`` + ``expected_behavior``) or ``human_correction`` (payload ``input`` +
``corrected_output``) becomes one deterministic regression case, and an eval set grows
by such cases idempotently. The case id is a domain-separated digest of the
observation's canonical digest, so the same record always yields the same case and the
stable split assigns it once; lineage keeps that digest with ``raw.ref``/``raw.hash``.

Known gaps (gates.md row 29 stays planned): no ingest codec emits these two kinds yet, so
observations are constructed by hand; the payload keys were chosen on the owner's behalf;
there is no persisted eval-set contract or codec; ``raw.hash`` is carried, not re-checked
against the artifact bytes; ``expected_behavior`` (a description) and
``corrected_output`` (an output) are kept apart only by ``kind``."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, TypeGuard

from underwrite_core.canonical import (
    CanonicalizationError,
    canonical_bytes,
    content_digest,
    tagged_digest,
)

IncidentKind = Literal["incident_record", "human_correction"]
IncidentErrorCode = Literal[
    "INVALID_OBSERVATION", "UNSUPPORTED_KIND", "MISSING_PAYLOAD_KEY", "CONFLICTING_CASE"
]

PAYLOAD_KEYS: Final[Mapping[IncidentKind, tuple[str, str]]] = {
    "incident_record": ("input", "expected_behavior"),
    "human_correction": ("input", "corrected_output"),
}
CASE_TAG: Final = "underwrite.regression_case.v1"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REF_RE = re.compile(r".+", re.DOTALL)


class IncidentInputError(ValueError):
    """An observation that cannot become a regression case; nothing partial is returned."""

    def __init__(self, code: IncidentErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class Lineage:
    """Where a case came from: the observation's digest and its raw reference."""

    observation_digest: str
    raw_ref: str
    raw_hash: str


@dataclass(frozen=True)
class RegressionCase:
    """One regression case; ``input``/``expected`` are private copies of the payload."""

    case_id: str
    kind: IncidentKind
    input: Any
    expected: Any
    lineage: Lineage


def _require(condition: bool, code: IncidentErrorCode, detail: str) -> None:
    if not condition:
        raise IncidentInputError(code, detail)


def _is_object(value: object) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping)


def _object(value: object, location: str) -> Mapping[str, Any]:
    if not _is_object(value):
        raise IncidentInputError("INVALID_OBSERVATION", location)
    return value


def _text(value: object, pattern: re.Pattern[str], location: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise IncidentInputError("INVALID_OBSERVATION", location)
    return value


def _kind(value: object) -> IncidentKind:
    for kind in PAYLOAD_KEYS:
        if value == kind:
            return kind
    raise IncidentInputError("UNSUPPORTED_KIND", repr(value))


def regression_case(observation: object) -> RegressionCase:
    """Build the regression case one incident or correction observation records."""
    record = _object(observation, "/")
    _require(record.get("schema") == "observation.v1", "INVALID_OBSERVATION", "/schema")
    kind = _kind(record.get("kind"))
    payload, raw = _object(record.get("payload"), "/payload"), _object(record.get("raw"), "/raw")
    lineage_ref = _text(raw.get("ref"), _REF_RE, "/raw/ref")
    raw_hash = _text(raw.get("hash"), _DIGEST_RE, "/raw/hash")
    keys = PAYLOAD_KEYS[kind]
    for key in keys:
        _require(key in payload, "MISSING_PAYLOAD_KEY", key)
    try:
        digest = content_digest(dict(record))
    except CanonicalizationError as error:
        raise IncidentInputError("INVALID_OBSERVATION", "/") from error
    # Canonical bytes round-trip: private copies the caller can neither see nor mutate.
    copies = [json.loads(canonical_bytes(payload[key])) for key in keys]
    return RegressionCase(
        tagged_digest(CASE_TAG, digest.encode()),
        kind,
        copies[0],
        copies[1],
        Lineage(digest, lineage_ref, raw_hash),
    )


def extend_eval_set(
    eval_set: Sequence[RegressionCase], cases: Sequence[RegressionCase]
) -> tuple[RegressionCase, ...]:
    """Append the cases not yet present, in order; re-adding a present case is a no-op.

    Two different cases under one id are refused rather than silently kept or replaced.
    """
    merged: dict[str, RegressionCase] = {}
    for case in (*eval_set, *cases):
        present = merged.setdefault(case.case_id, case)
        _require(present == case, "CONFLICTING_CASE", case.case_id)
    return tuple(merged.values())
