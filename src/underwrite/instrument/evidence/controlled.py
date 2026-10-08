"""Audit recorded comparison conditions without inventing missing evidence.

A controlled comparison varies exactly one supported condition axis; identical
conditions have no varied axis. The result describes recorded inputs and cannot
prove that a producer actually ran under those conditions."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, TypeGuard, cast

ConditionValue = str | int | float | None
Side = Literal["baseline", "candidate"]

_FIELD_TYPES: Final[dict[str, tuple[type, ...]]] = {
    "suite": (str,),
    "prompt_version": (str,),
    "model": (str,),
    "gen_params.temperature": (int, float),
    "gen_params.top_p": (int, float),
    "gen_params.max_tokens": (int,),
    "gen_params.think": (str,),
    "gen_params.seed": (int, type(None)),
    "gen_params.num_ctx": (int, type(None)),
    "gen_params.timeout_s": (int, float),
}


class AuditReason(StrEnum):
    """Descriptive reasons why recorded conditions cannot isolate one axis."""

    NO_VARIED_AXIS = "no_varied_axis"
    MULTIPLE_VARIED_AXES = "multiple_varied_axes"
    SUITE_MISMATCH = "suite_mismatch"
    MISSING_CONDITIONS = "missing_conditions"
    UNKNOWN_CONDITIONS = "unknown_conditions"


@dataclass(frozen=True)
class ConditionDelta:
    """Known recorded values that differ along one condition axis."""

    axis: str
    baseline: ConditionValue
    candidate: ConditionValue


@dataclass(frozen=True)
class ConditionIssue:
    """The side and field whose condition could not be established."""

    side: Side
    field: str
    reason: Literal["missing", "unknown"]


@dataclass(frozen=True)
class ConditionAudit:
    """Descriptive condition evidence; eligibility carries no authority."""

    differences: tuple[ConditionDelta, ...]
    suite_mismatch: ConditionDelta | None
    unverifiable: tuple[ConditionIssue, ...]

    @property
    def changed_axes(self) -> tuple[str, ...]:
        return _changed_axes(self)

    @property
    def reasons(self) -> tuple[AuditReason, ...]:
        return _reasons(self)

    @property
    def eligible(self) -> bool:
        """Whether these recorded conditions isolate exactly one varied axis."""
        return _eligible(self)


def _changed_axes(audit: ConditionAudit) -> tuple[str, ...]:
    return tuple(delta.axis for delta in audit.differences)


def _reasons(audit: ConditionAudit) -> tuple[AuditReason, ...]:
    """Every reason these conditions fail to isolate one axis, never just the first.

    NO_VARIED_AXIS is the fallback: it is reported only when nothing else went wrong
    and nothing varied, so "no axis moved" is never stacked on top of a real defect.
    """
    reasons: list[AuditReason] = []
    if any(issue.reason == "missing" for issue in audit.unverifiable):
        reasons.append(AuditReason.MISSING_CONDITIONS)
    if any(issue.reason == "unknown" for issue in audit.unverifiable):
        reasons.append(AuditReason.UNKNOWN_CONDITIONS)
    if audit.suite_mismatch is not None:
        reasons.append(AuditReason.SUITE_MISMATCH)
    if len(audit.differences) > 1:
        reasons.append(AuditReason.MULTIPLE_VARIED_AXES)
    if not reasons and not audit.differences:
        reasons.append(AuditReason.NO_VARIED_AXIS)
    return tuple(reasons)


def _eligible(audit: ConditionAudit) -> bool:
    return not audit.reasons


def _known_value(value: object, expected: tuple[type, ...]) -> TypeGuard[ConditionValue]:
    # Exact types prevent bool == int and custom equality from hiding changes.
    if type(value) not in expected:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return not isinstance(value, float) or math.isfinite(value)


def _generation_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping) and all(
        isinstance(key, str) for key in cast(Mapping[object, object], value)
    ):
        return cast(Mapping[str, object], value)
    return None


def _read_conditions(
    info: Mapping[str, object], side: Side
) -> tuple[dict[str, ConditionValue], tuple[ConditionIssue, ...]]:
    values: dict[str, ConditionValue] = {}
    issues: list[ConditionIssue] = []
    gen = _generation_mapping(info.get("gen_params"))
    if gen is None:
        reason = "unknown" if "gen_params" in info else "missing"
        issues.append(ConditionIssue(side, "gen_params", reason))
    for field, expected in _FIELD_TYPES.items():
        is_generation = field.startswith("gen_params.")
        record = gen if is_generation else info
        if record is None:
            continue
        key = field.removeprefix("gen_params.")
        if key not in record:
            issues.append(ConditionIssue(side, field, "missing"))
        elif not _known_value(value := record[key], expected):
            issues.append(ConditionIssue(side, field, "unknown"))
        else:
            values[field] = value
    if gen is not None:
        for key in sorted(gen):
            field = f"gen_params.{key}"
            if field not in _FIELD_TYPES:
                issues.append(ConditionIssue(side, field, "unknown"))
    return values, tuple(issues)


def audit_conditions(
    baseline: Mapping[str, object], candidate: Mapping[str, object]
) -> ConditionAudit:
    """Compare known source-shaped conditions without inferring missing values.

    ``prompt_version`` is reported as the ``prompt`` axis. Suite differences
    remain separate because they change the case population. Metadata outside
    these condition fields (run IDs, durations, and results) is not compared.
    """
    values_a, issues_a = _read_conditions(baseline, "baseline")
    values_b, issues_b = _read_conditions(candidate, "candidate")
    differences: list[ConditionDelta] = []
    suite_mismatch: ConditionDelta | None = None
    for field, value_a in values_a.items():
        if field not in values_b or value_a == values_b[field]:
            continue
        axis = "prompt" if field == "prompt_version" else field
        delta = ConditionDelta(axis, value_a, values_b[field])
        if field == "suite":
            suite_mismatch = delta
        else:
            differences.append(delta)
    return ConditionAudit(tuple(differences), suite_mismatch, issues_a + issues_b)
