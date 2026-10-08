"""Assign stable exploration and confirmation partitions by salted case hash.

The author supplies the salt and confirmation fraction. Deterministic assignment
does not prove that cases were selected before results were observed."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal

SplitName = Literal["explore", "confirm"]
SplitErrorCode = Literal[
    "INVALID_POPULATION", "INVALID_SALT", "INVALID_CASE_ID", "INVALID_FRACTION", "DUPLICATE_CASE_ID"
]


class SplitInputError(ValueError):
    """Malformed input; no partial assignment is returned."""

    def __init__(self, code: SplitErrorCode, case_id: str | None = None) -> None:
        self.code = code
        self.case_id = case_id
        super().__init__(code if case_id is None else f"{code}: {case_id!r}")


@dataclass(frozen=True)
class SplitAssignment:
    """One case's descriptive partition assignment."""

    case_id: str
    split: SplitName


@dataclass(frozen=True)
class SplitEvidence:
    """Owned assignments and context; derived partitions never duplicate state."""

    salt: str
    confirm_fraction: float
    assignments: tuple[SplitAssignment, ...]
    method: Literal["sha256-delimited-uint64-float-v1"] = field(
        default="sha256-delimited-uint64-float-v1", init=False
    )
    limitations: tuple[str, ...] = field(
        default=(
            "delimiter_encoding_can_collide_across_salt_case_pairs",
            "salt_context_requires_external_governance",
            "confirm_secrecy_and_selection_not_enforced",
            "holdout_adequacy_not_established",
        ),
        init=False,
    )

    @property
    def explore_case_ids(self) -> tuple[str, ...]:
        return _explore_case_ids(self)

    @property
    def confirm_case_ids(self) -> tuple[str, ...]:
        return _confirm_case_ids(self)

    @property
    def n_cases(self) -> int:
        return len(self.assignments)

    @property
    def explore_count(self) -> int:
        return len(self.explore_case_ids)

    @property
    def confirm_count(self) -> int:
        return len(self.confirm_case_ids)

    @property
    def reason(self) -> Literal["empty_population"] | None:
        return _reason(self)


def _case_ids(evidence: SplitEvidence, split: SplitName) -> tuple[str, ...]:
    """Return IDs assigned to one partition, in assignment order."""
    return tuple(item.case_id for item in evidence.assignments if item.split == split)


def _explore_case_ids(evidence: SplitEvidence) -> tuple[str, ...]:
    return _case_ids(evidence, "explore")


def _confirm_case_ids(evidence: SplitEvidence) -> tuple[str, ...]:
    return _case_ids(evidence, "confirm")


def _reason(evidence: SplitEvidence) -> Literal["empty_population"] | None:
    return "empty_population" if not evidence.assignments else None


def _validate_text(value: str, code: SplitErrorCode) -> None:
    if type(value) is not str or unicodedata.normalize("NFC", value) != value:
        raise SplitInputError(code)
    if code == "INVALID_CASE_ID" and not value.strip():
        raise SplitInputError(code)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise SplitInputError(code) from error


def assign_split(case_ids: Sequence[str], *, salt: str, confirm_fraction: float) -> SplitEvidence:
    """Assign exact list/tuple IDs with explicit policy and source-compatible bytes.

    A finite sample need not realize the requested fraction or populate both
    partitions. The function neither chooses a candidate nor grants release rights.
    """
    if type(case_ids) not in (list, tuple):
        raise SplitInputError("INVALID_POPULATION")
    _validate_text(salt, "INVALID_SALT")
    # Range first safely rejects arbitrarily large ints without float conversion.
    if type(confirm_fraction) not in (int, float) or not 0 < confirm_fraction < 1:
        raise SplitInputError("INVALID_FRACTION")
    seen: set[str] = set()
    for case_id in case_ids:
        _validate_text(case_id, "INVALID_CASE_ID")
        if case_id in seen:
            raise SplitInputError("DUPLICATE_CASE_ID", case_id)
        seen.add(case_id)
    assignments: list[SplitAssignment] = []
    for case_id in sorted(seen):
        digest = sha256(f"{salt}|{case_id}".encode()).digest()
        value = int.from_bytes(digest[:8], "big") / float(1 << 64)
        name: SplitName = "confirm" if value < confirm_fraction else "explore"
        assignments.append(SplitAssignment(case_id, name))
    return SplitEvidence(salt, confirm_fraction, tuple(assignments))
