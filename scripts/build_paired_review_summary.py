"""Generate the bounded paired-review projection from its authored full contract."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/paired_review.v1.schema.json"
TARGET = ROOT / "contracts/paired_review_summary.v1.schema.json"
CLASSIFICATIONS = (
    "intended_change",
    "unchanged_treatment",
    "invariant_changed",
    "invariant_unchanged",
    "unknown",
)


def _state_counts(
    count_fields: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["state", *count_fields],
        "properties": {
            "state": {"enum": ["absent", "present"]},
            **{key: {"oneOf": [{"type": "null"}, value]} for key, value in count_fields.items()},
        },
        "oneOf": [
            {
                "properties": {
                    "state": {"const": "absent"},
                    **{key: {"type": "null"} for key in count_fields},
                }
            },
            {"properties": {"state": {"const": "present"}, **count_fields}},
        ],
    }


def _source_rule(rules: list[dict[str, Any]], field: str) -> dict[str, Any]:
    matches = [
        rule
        for rule in rules
        if field in rule.get("if", {}).get("required", [])
        or field in rule.get("if", {}).get("properties", {})
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one conditional rule for {field}")
    return matches[0]


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    fields = cast(dict[str, Any], full["properties"])
    definitions = cast(dict[str, Any], full["$defs"])
    rules = cast(list[dict[str, Any]], full["allOf"])
    base_limits = deepcopy(rules[:6])
    condition_rule = deepcopy(_source_rule(rules, "conditions"))
    history_rule = deepcopy(_source_rule(rules, "evaluation_history"))
    partition_rule = deepcopy(_source_rule(rules, "case_partition"))
    condition_rule["if"] = {
        "properties": {"declared_conditions": {"properties": {"coverage": {"const": "unknown"}}}}
    }
    history_rule["if"] = {
        "properties": {"history": {"properties": {"state": {"const": "present"}}}}
    }
    partition_rule["if"] = {
        "properties": {"partition": {"properties": {"state": {"const": "present"}}}}
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:paired_review_summary.v1",
        "title": "paired_review_summary.v1",
        "description": (
            "Content-minimized summary of one exact paired review; declared strings "
            "and per-group details are omitted, and no decision authority is granted."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema",
            "view",
            "paired_evidence_ref",
            "context_digest",
            "declared_conditions",
            "history",
            "partition",
            "declarations_verified",
            "actual_conditions_verified",
            "semantic_equivalence_verified",
            "detail_state",
            "limitations",
        ],
        "properties": {
            "schema": {"const": "paired_review_summary.v1"},
            "view": {"const": "summary"},
            "paired_evidence_ref": deepcopy(fields["paired_evidence_ref"]),
            "context_digest": deepcopy(fields["context_digest"]),
            "declared_conditions": {
                "type": "object",
                "additionalProperties": False,
                "required": ["coverage", "total", "classifications"],
                "properties": {
                    "coverage": deepcopy(fields["conditions"]["properties"]["coverage"]),
                    "total": {"type": "integer", "minimum": 0, "maximum": 32},
                    "classifications": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(CLASSIFICATIONS),
                        "properties": {
                            name: {"type": "integer", "minimum": 0, "maximum": 32}
                            for name in CLASSIFICATIONS
                        },
                    },
                },
            },
            "history": _state_counts(
                {
                    "record_count": {"type": "integer", "minimum": 0, "maximum": 32},
                    "selector_coverage": deepcopy(
                        definitions["evaluationHistory"]["properties"]["selector_coverage"]
                    ),
                }
            ),
            "partition": _state_counts(
                {
                    "group_count": {"type": "integer", "minimum": 1, "maximum": 33},
                    "case_count": {"type": "integer", "minimum": 0, "maximum": 128},
                }
            ),
            "declarations_verified": deepcopy(fields["declarations_verified"]),
            "actual_conditions_verified": deepcopy(fields["actual_conditions_verified"]),
            "semantic_equivalence_verified": deepcopy(fields["semantic_equivalence_verified"]),
            "detail_state": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "condition_values",
                    "history_records",
                    "partition_groups",
                    "full_view",
                ],
                "properties": {
                    "condition_values": {"const": "intentionally_omitted"},
                    "history_records": {"enum": ["intentionally_omitted", "not_applicable"]},
                    "partition_groups": {"enum": ["intentionally_omitted", "not_applicable"]},
                    "full_view": {"const": "review-pair --view full"},
                },
            },
            "limitations": deepcopy(fields["limitations"]),
        },
        "allOf": [
            *base_limits,
            condition_rule,
            history_rule,
            partition_rule,
        ],
        "$defs": {"digest": deepcopy(definitions["digest"])},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("paired_review_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
