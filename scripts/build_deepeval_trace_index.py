"""Generate the selected DeepEval trace-index contract from its pure owner."""

from __future__ import annotations

from pathlib import Path

if __package__:
    from ._record_schema import schema_main
    from ._schema_compose import object_schema as _object
    from .build_deepeval_accounting import source_identity
else:
    from _record_schema import schema_main
    from _schema_compose import object_schema as _object
    from build_deepeval_accounting import source_identity

from underwrite.instrument.evidence.deepeval_accounting import MAX_CASES
from underwrite.instrument.evidence.deepeval_compat import (
    TRACE_LIMITATIONS_EXTRA,
    TRACE_READER,
    TRACE_SCOPE_EXTRA,
)
from underwrite.instrument.evidence.deepeval_compat import (
    TRACE_SCHEMA as SCHEMA,
)
from underwrite.instrument.evidence.deepeval_trace import (
    GROUP_AVAILABILITY,
    GROUP_TYPES,
    LIMITATIONS,
    MAX_SPANS,
    NEXT_ACTIONS,
    PARENT_STATES,
    RULE_PROFILE,
    SCOPE,
    SPAN_TYPES,
    STATUSES,
    TRACE_AVAILABILITY,
)

ROOT = Path(__file__).resolve().parents[1]


def _count(maximum: int) -> dict[str, object]:
    return {"type": "integer", "minimum": 0, "maximum": maximum}


def _tag(field: str, value: str, properties: dict[str, object]) -> dict[str, object]:
    return {
        "if": {"properties": {field: {"const": value}}, "required": [field]},
        "then": {"properties": properties},
    }


def _span_pointer() -> dict[str, object]:
    groups = "|".join(GROUP_TYPES)
    return {
        "type": "string",
        "pattern": rf"^/testCases/(0|[1-9][0-9]*)/trace/({groups})/(0|[1-9][0-9]*)(?![\s\S])",
    }


def _groups() -> dict[str, object]:
    return _object({name: {"enum": list(GROUP_AVAILABILITY)} for name in GROUP_TYPES})


def _span() -> dict[str, object]:
    row = _object(
        {
            "span_index": _count(MAX_SPANS - 1),
            "pointer": _span_pointer(),
            "group": {"enum": list(GROUP_TYPES)},
            "group_index": _count(MAX_SPANS - 1),
            "declared_type": {"enum": list(SPAN_TYPES)},
            "source_status": {"enum": list(STATUSES)},
            "parent_state": {"enum": list(PARENT_STATES)},
            "parent_pointer": {"anyOf": [{"type": "null"}, _span_pointer()]},
            "cycle_member": {"type": "boolean"},
            "group_type_mismatch": {"type": "boolean"},
        }
    )
    row["allOf"] = [
        _tag("parent_state", "resolved", {"parent_pointer": _span_pointer()}),
        *(
            _tag(
                "parent_state",
                state,
                {"parent_pointer": {"type": "null"}, "cycle_member": {"const": False}},
            )
            for state in ("not_reported", "unresolved")
        ),
    ]
    return row


def _case() -> dict[str, object]:
    row = _object(
        {
            "case_index": _count(MAX_CASES - 1),
            "pointer": {
                "type": "string",
                "pattern": r"^/testCases/(0|[1-9][0-9]*)(?![\s\S])",
            },
            "source_success": {"type": ["boolean", "null"]},
            "trace_availability": {"enum": list(TRACE_AVAILABILITY)},
            "trace_status": {"enum": [*STATUSES, None]},
            "groups": _groups(),
            "spans": {"type": "array", "maxItems": MAX_SPANS, "items": _span()},
        }
    )
    row["allOf"] = [
        *(
            _tag(
                "trace_availability",
                availability,
                {
                    "trace_status": {"type": "null"},
                    "groups": _object({name: {"const": "absent"} for name in GROUP_TYPES}),
                    "spans": {"maxItems": 0},
                },
            )
            for availability in ("absent", "null")
        ),
    ]
    return row


def _counts() -> dict[str, object]:
    return _object(
        {
            "cases": _object(
                {
                    "total": _count(MAX_CASES),
                    "source_true": _count(MAX_CASES),
                    "source_false": _count(MAX_CASES),
                    "source_unknown": _count(MAX_CASES),
                }
            ),
            "traces": _object(
                {
                    **{state: _count(MAX_CASES) for state in TRACE_AVAILABILITY},
                    "status_success": _count(MAX_CASES),
                    "status_errored": _count(MAX_CASES),
                    "status_unknown": _count(MAX_CASES),
                }
            ),
            "spans": _object(
                {
                    "total": _count(MAX_SPANS),
                    "by_group": _object({name: _count(MAX_SPANS) for name in GROUP_TYPES}),
                    "by_declared_type": _object({name: _count(MAX_SPANS) for name in SPAN_TYPES}),
                    "status_success": _count(MAX_SPANS),
                    "status_errored": _count(MAX_SPANS),
                    "parent_resolved": _count(MAX_SPANS),
                    "parent_unresolved": _count(MAX_SPANS),
                    "parent_not_reported": _count(MAX_SPANS),
                    "cycle_members": _count(MAX_SPANS),
                    "group_type_mismatch": _count(MAX_SPANS),
                }
            ),
        }
    )


def build_schema() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:underwrite:contract:{SCHEMA}",
        "title": SCHEMA,
        "description": "Selected DeepEval case and span structure; producer version unknown.",
        **_object(
            {
                "schema": {"const": SCHEMA},
                "source": source_identity(),
                "raw_hash": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}(?![\\s\\S])"},
                "rule_profile": {"const": RULE_PROFILE},
                "scope": _object(
                    {key: {"const": value} for key, value in {**SCOPE, **TRACE_SCOPE_EXTRA}.items()}
                ),
                "cases": {"type": "array", "maxItems": MAX_CASES, "items": _case()},
                "counts": _counts(),
                "limitations": {"const": [*LIMITATIONS, *TRACE_LIMITATIONS_EXTRA]},
                "next_actions": {
                    "type": "array",
                    "maxItems": len(NEXT_ACTIONS),
                    "items": {"enum": list(NEXT_ACTIONS)},
                    "uniqueItems": True,
                },
                "reader_profile": {"const": TRACE_READER},
            }
        ),
    }


def main() -> int:
    return schema_main(build_schema(), ROOT / f"contracts/{SCHEMA}.schema.json", __doc__)


if __name__ == "__main__":
    raise SystemExit(main())
