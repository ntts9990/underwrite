"""Construct current DeepEval accounting branches from actual serialized metric shapes."""

from __future__ import annotations

if __package__:
    from ._schema_compose import object_schema as _object
else:
    from _schema_compose import object_schema as _object

from underwrite_core.canonical import SAFE_INT_MAX

from underwrite.instrument.evidence.deepeval_accounting import (
    LIMITATIONS,
    LIMITATIONS_V2,
    MAX_CASES,
    MAX_ERROR_LENGTH,
    MAX_METRICS,
    MAX_NAME_LENGTH,
    NEXT_ACTIONS,
    RULE_PROFILE,
    RULE_PROFILE_V2,
    RULES,
    SCOPE,
)
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_LIMITATIONS_EXTRA,
    ACCOUNTING_SCHEMA,
    ACCOUNTING_SCOPE_EXTRA,
    EMPTY_LIMITATION,
    FLAKY_READER,
    NO_FLAKY_READER,
    SOURCE_FORMAT,
)


def source_identity() -> dict[str, object]:
    return _object(
        {
            "format": {"const": SOURCE_FORMAT},
            "producer_version": {"type": "null"},
            "producer_version_state": {"const": "unknown"},
            "claims_verified": {"const": False},
        }
    )


def _limitations(values: list[str]) -> dict[str, object]:
    return {"type": "array", "const": values, "maxItems": len(values), "items": {"enum": values}}


def _count(maximum: int = SAFE_INT_MAX) -> dict[str, object]:
    return {"type": "integer", "minimum": 0, "maximum": maximum}


def _presence() -> dict[str, object]:
    return {
        "oneOf": [
            _object({"present": {"const": False}}),
            _object({"present": {"const": True}, "value": _count()}),
        ]
    }


def build_branch(*, flaky: bool) -> dict[str, object]:
    limitations = [*(LIMITATIONS_V2 if flaky else LIMITATIONS), *ACCOUNTING_LIMITATIONS_EXTRA]
    reader = FLAKY_READER if flaky else NO_FLAKY_READER
    selected_rule = RULE_PROFILE_V2 if flaky else RULE_PROFILE
    name = {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH}
    metric_branches: list[dict[str, object]] = []
    for state, rule in RULES.items():
        fields: dict[str, object] = {
            "metric_index": _count(MAX_METRICS - 1),
            "name": name,
            "state": {"const": state},
            "applied_rule": {"const": rule},
            "source_success": {"type": "boolean"},
            "threshold": {"type": "number"},
        }
        if flaky:
            fields["source_success"] = {
                "type": ["boolean", "null"],
                "description": "null denotes an absent source success key, not a false verdict.",
            }
            fields["threshold"] = {
                "type": ["number", "null"],
                "description": "null denotes an absent source threshold key, not a zero threshold.",
            }
            fields["source_flaky"] = {
                "type": "boolean",
                "description": "Producer gating declaration, not measured nondeterminism.",
            }
        if state == "scored":
            fields["score"] = {"type": "number"}
        elif state == "error":
            fields["error"] = {"type": "string", "maxLength": MAX_ERROR_LENGTH}
        metric_branches.append(_object(fields))
    case_fields: dict[str, object] = {
        "case_index": _count(MAX_CASES - 1),
        "name": name,
        "order": _presence(),
        "source_success": {
            "type": ["boolean", "null"],
            "description": (
                "null denotes an absent source success key; a present null source flag is rejected."
            ),
        },
        "metrics": {
            "type": "array",
            "maxItems": MAX_METRICS,
            "items": {"oneOf": metric_branches},
        },
    }
    if flaky:
        case_fields["source_flaky"] = {
            "type": "boolean",
            "description": "Producer gating declaration, not measured nondeterminism.",
        }
    case = _object(case_fields)
    case_counts = ("total", "source_true", "source_false", "source_success_absent")
    metric_counts = (
        "total",
        "scored",
        "error",
        "unscored",
        "observed_zero",
        "scored_source_true",
        "scored_source_false",
    )
    if flaky:
        case_counts += ("source_flaky_true",)
        metric_counts += ("scored_source_success_absent", "source_flaky_true")
    digest = {"type": "string", "pattern": "^sha256:[a-f0-9]{64}(?![\\s\\S])"}
    relation = {"enum": ["matches", "differs", "not_declared", "not_comparable"]}
    schema = _object(
        {
            "schema": {"const": ACCOUNTING_SCHEMA},
            "rule_profile": {"const": selected_rule},
            "source": source_identity(),
            "raw_hash": digest,
            "scope": _object(
                {
                    key: {"const": value}
                    for key, value in {**SCOPE, **ACCOUNTING_SCOPE_EXTRA}.items()
                }
            ),
            "cases": {"type": "array", "maxItems": MAX_CASES, "items": case},
            "counts": _object(
                {
                    "cases": _object({key: _count(MAX_CASES) for key in case_counts}),
                    "metrics": _object({key: _count(MAX_METRICS) for key in metric_counts}),
                }
            ),
            "declared_case_counts": _object(
                {
                    "passed": _presence(),
                    "failed": _presence(),
                    "comparison": _object({"passed": relation, "failed": relation}),
                }
            ),
            "limitations": _limitations(limitations)
            if flaky
            else {
                "type": "array",
                "maxItems": len(limitations) + 1,
                "items": {"enum": [*limitations, EMPTY_LIMITATION]},
            },
            "next_actions": {
                "type": "array",
                "maxItems": len(NEXT_ACTIONS),
                "items": {"type": "string", "enum": list(NEXT_ACTIONS)},
                "uniqueItems": True,
            },
            "reader_profile": {"const": reader},
        }
    )
    if not flaky:
        schema["allOf"] = [
            {
                "if": {
                    "properties": {
                        "counts": {"properties": {"cases": {"properties": {"total": {"const": 0}}}}}
                    }
                },
                "then": {
                    "properties": {"limitations": _limitations([*limitations, EMPTY_LIMITATION])}
                },
                "else": {"properties": {"limitations": _limitations(limitations)}},
            }
        ]
    return schema


if __name__ == "__main__":
    raise SystemExit("Use build_deepeval_compat.py to generate current DeepEval contracts.")
