"""Generate the descriptive accounting contract from its owned rules and limits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from underwrite_core.canonical import SAFE_INT_MAX

from underwrite.instrument.evidence.deepeval_accounting import (
    LIMITATIONS,
    MAX_CASES,
    MAX_ERROR_LENGTH,
    MAX_METRICS,
    MAX_NAME_LENGTH,
    NEXT_ACTIONS,
    RULE_PROFILE,
    RULES,
    SCOPE,
)
from underwrite.instrument.ingest.deepeval_accounting import SOURCE

ROOT = Path(__file__).resolve().parents[1]


def _object(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _count(maximum: int = SAFE_INT_MAX) -> dict[str, object]:
    return {"type": "integer", "minimum": 0, "maximum": maximum}


def _presence() -> dict[str, object]:
    return {
        "oneOf": [
            _object({"present": {"const": False}}),
            _object({"present": {"const": True}, "value": _count()}),
        ]
    }


def build_schema() -> dict[str, object]:
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
        if state == "scored":
            fields["score"] = {"type": "number"}
        elif state == "error":
            fields["error"] = {"type": "string", "maxLength": MAX_ERROR_LENGTH}
        metric_branches.append(_object(fields))
    case = _object(
        {
            "case_index": _count(MAX_CASES - 1),
            "name": name,
            "order": _presence(),
            "source_success": {
                "type": ["boolean", "null"],
                "description": (
                    "null denotes an absent source success key; "
                    "a present null source flag is rejected."
                ),
            },
            "metrics": {
                "type": "array",
                "maxItems": MAX_METRICS,
                "items": {"oneOf": metric_branches},
            },
        }
    )
    digest = {"type": "string", "pattern": "^sha256:[a-f0-9]{64}(?![\\s\\S])"}
    relation = {"enum": ["matches", "differs", "not_declared", "not_comparable"]}
    schema = _object(
        {
            "schema": {"const": "deepeval_accounting.v1"},
            "rule_profile": {"const": RULE_PROFILE},
            "source": _object(
                {
                    "format": {"const": SOURCE[0]},
                    "format_version": {"const": SOURCE[1]},
                    "claims_verified": {"const": False},
                }
            ),
            "raw_hash": digest,
            "observation_digest": digest,
            "scope": _object({key: {"const": value} for key, value in SCOPE.items()}),
            "cases": {"type": "array", "maxItems": MAX_CASES, "items": case},
            "counts": _object(
                {
                    "cases": _object(
                        {
                            key: _count(MAX_CASES)
                            for key in (
                                "total",
                                "source_true",
                                "source_false",
                                "source_success_absent",
                            )
                        }
                    ),
                    "metrics": _object(
                        {
                            key: _count(MAX_METRICS)
                            for key in (
                                "total",
                                "scored",
                                "error",
                                "unscored",
                                "observed_zero",
                                "scored_source_true",
                                "scored_source_false",
                            )
                        }
                    ),
                }
            ),
            "declared_case_counts": _object(
                {
                    "passed": _presence(),
                    "failed": _presence(),
                    "comparison": _object({"passed": relation, "failed": relation}),
                }
            ),
            "limitations": {
                "type": "array",
                "const": list(LIMITATIONS),
                "maxItems": len(LIMITATIONS),
                "items": {"enum": list(LIMITATIONS)},
            },
            "next_actions": {
                "type": "array",
                "maxItems": len(NEXT_ACTIONS),
                "items": {"type": "string", "enum": list(NEXT_ACTIONS)},
                "uniqueItems": True,
            },
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:deepeval_accounting.v1",
        "title": "deepeval_accounting.v1",
        "description": "Descriptive source accounting; no measurement or approval authority.",
        **schema,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / "contracts/deepeval_accounting.v1.schema.json"
    rendered = json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        return 0 if target.exists() and target.read_text(encoding="utf-8") == rendered else 1
    target.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
