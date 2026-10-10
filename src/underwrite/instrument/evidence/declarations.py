"""Compare supplied declarations without claiming the executions matched them."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal, TypedDict, cast

from underwrite.instrument.evidence.controlled import audit_conditions

Relation = Literal["same", "different", "unknown"]
Side = Literal["baseline", "candidate"]
Scalar = str | int | float | bool

METRIC_FIELDS = (
    "name",
    "unit",
    "higher_is_better",
    "scope",
    "denominator",
    "aggregation",
    "evaluator",
    "evaluator_version",
    "policy",
    "threshold",
)


class DeclarationError(ValueError):
    """A direct caller supplied an unusable declaration object."""


class Difference(TypedDict):
    field: str
    baseline: Scalar
    candidate: Scalar


class Unknown(TypedDict):
    side: Side
    field: str
    reason: Literal["missing", "unknown"]


class Comparison(TypedDict):
    declared_relation: Relation
    differences: list[Difference]
    unknowns: list[Unknown]


def _conditions(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DeclarationError("INVALID_CONDITIONS")
    conditions = cast(Mapping[str, object], value)
    # controlled.py treats null seed/num_ctx as known values. In this pilot every
    # null declaration means unknown, including those two optional generation axes.
    normalized = {key: object() if item is None else item for key, item in conditions.items()}
    gen = conditions.get("gen_params")
    if isinstance(gen, Mapping):
        generation = cast(Mapping[str, object], gen)
        normalized["gen_params"] = {
            key: object() if item is None else item for key, item in generation.items()
        }
    return normalized


def _metric(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DeclarationError("INVALID_METRIC")
    return cast(Mapping[str, object], value)


def _known_metric(metric: Mapping[str, object], field: str) -> Scalar | None:
    value = metric.get(field)
    if value is None:
        return None
    if field == "higher_is_better":
        if type(value) is not bool:
            raise DeclarationError("INVALID_METRIC_FIELD")
    elif field == "threshold":
        if type(value) not in (int, float):
            raise DeclarationError("INVALID_METRIC_FIELD")
        try:
            if not math.isfinite(cast(int | float, value)):
                raise DeclarationError("INVALID_METRIC_FIELD")
        except OverflowError as exc:
            raise DeclarationError("INVALID_METRIC_FIELD") from exc
    elif type(value) is not str:
        raise DeclarationError("INVALID_METRIC_FIELD")
    if isinstance(value, str) and not value.strip():
        return None
    return cast(Scalar, value)


def compare_declarations(
    baseline_conditions: object,
    candidate_conditions: object,
    baseline_metric: object,
    candidate_metric: object,
) -> Comparison:
    """Keep known differences even when other declarations remain unknown.

    `same` means only these supplied fields match. It does not verify runtime
    conditions, population identity, score meaning, or independent provenance.
    """
    condition_a, condition_b = _conditions(baseline_conditions), _conditions(
        candidate_conditions
    )
    metric_a, metric_b = _metric(baseline_metric), _metric(candidate_metric)
    audit = audit_conditions(condition_a, condition_b)
    differences: list[Difference] = []
    unknowns: list[Unknown] = [
        {
            "side": issue.side,
            "field": f"conditions.{issue.field}",
            "reason": issue.reason,
        }
        for issue in audit.unverifiable
    ]
    if audit.suite_mismatch is not None:
        differences.append(
            {
                "field": "conditions.suite",
                "baseline": cast(Scalar, audit.suite_mismatch.baseline),
                "candidate": cast(Scalar, audit.suite_mismatch.candidate),
            }
        )
    for delta in audit.differences:
        field = "prompt_version" if delta.axis == "prompt" else delta.axis
        differences.append(
            {
                "field": f"conditions.{field}",
                "baseline": cast(Scalar, delta.baseline),
                "candidate": cast(Scalar, delta.candidate),
            }
        )
    for field in METRIC_FIELDS:
        left, right = _known_metric(metric_a, field), _known_metric(metric_b, field)
        if left is None:
            unknowns.append(
                {
                    "side": "baseline",
                    "field": f"metric.{field}",
                    "reason": "missing" if field not in metric_a else "unknown",
                }
            )
        if right is None:
            unknowns.append(
                {
                    "side": "candidate",
                    "field": f"metric.{field}",
                    "reason": "missing" if field not in metric_b else "unknown",
                }
            )
        if left is not None and right is not None and left != right:
            differences.append(
                {"field": f"metric.{field}", "baseline": left, "candidate": right}
            )
    return {
        "declared_relation": "different" if differences else "unknown" if unknowns else "same",
        "differences": differences,
        "unknowns": unknowns,
    }
