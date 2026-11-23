"""Compose bounded DeepEval binding counts from common-result owners."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from underwrite.cli.audit_deepeval import BOUND_SUMMARY_LIMITATIONS

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "contracts/evidence_result.v2.schema.json"
INSPECTION = ROOT / "contracts/common_result_inspection_actions.v3.schema.json"
TARGET = ROOT / "contracts/deepeval_bound_summary.v1.schema.json"


def _object_variant(value: dict[str, Any]) -> dict[str, Any]:
    variants = cast(list[dict[str, Any]], value["oneOf"])
    matches = [item for item in variants if item.get("type") == "object"]
    if len(matches) != 1:
        raise ValueError("INSPECTION_COUNT_OBJECT_CHANGED")
    return deepcopy(matches[0])


def build_schema() -> dict[str, Any]:
    result = cast(dict[str, Any], json.loads(RESULT.read_text(encoding="utf-8")))
    inspection = cast(dict[str, Any], json.loads(INSPECTION.read_text(encoding="utf-8")))
    fields = cast(dict[str, Any], result["properties"])
    inspected = cast(dict[str, Any], inspection["properties"])
    definitions = cast(dict[str, Any], result["$defs"])
    return {
        "$schema": result["$schema"],
        "$id": "urn:underwrite:contract:deepeval_bound_summary.v1",
        "title": "deepeval_bound_summary.v1",
        "description": (
            "Selected DeepEval metric occurrences and owner-asserted binding counts. "
            "No item values, names, coordinates or owner assertion text are returned."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "view", "source", "declaration_source", "processing", "population",
            "availability_counts", "item_next_action_counts",
            "declared_semantic_binding_counts", "verification_entry_count",
            "limitations", "detail_state",
        ],
        "properties": {
            "schema": {"const": "deepeval_bound_summary.v1"},
            "view": {"const": "bound-summary"},
            "source": {
                "allOf": [
                    deepcopy(fields["source"]),
                    {
                        "type": "object",
                        "properties": {
                            "format": {"const": "deepeval.test-run"},
                            "identity": {"properties": {"kind": {"const": "single_file"}}},
                            "producer_version_state": {"const": "unknown"},
                            "producer_version": {"type": "null"},
                        },
                    },
                ]
            },
            "declaration_source": {
                "allOf": [
                    deepcopy(fields["declaration_source"]),
                    {
                        "type": "object",
                        "properties": {
                            "format": {"const": "deepeval_metric_bindings.v2"},
                            "authority": {"const": "owner_asserted_unverified"},
                        },
                    },
                ]
            },
            "processing": {
                "allOf": [
                    deepcopy(fields["processing"]),
                    {"properties": {"state": {"const": "analyzed"}}},
                ]
            },
            "population": {
                "allOf": [
                    deepcopy(fields["population"]),
                    {
                        "type": "object",
                        "properties": {
                            "unit": {"const": "metric_occurrence"},
                            "planned_count": {"type": "null"},
                        },
                    },
                ]
            },
            **{
                name: _object_variant(inspected[name])
                for name in (
                    "availability_counts", "item_next_action_counts",
                    "declared_semantic_binding_counts",
                )
            },
            "verification_entry_count": {"type": "integer", "minimum": 0},
            "limitations": {"const": list(BOUND_SUMMARY_LIMITATIONS)},
            "detail_state": {
                "type": "object",
                "additionalProperties": False,
                "required": ["item_values", "item_coordinates", "metric_names", "full_view"],
                "properties": {
                    "item_values": {"const": "intentionally_omitted"},
                    "item_coordinates": {"const": "intentionally_omitted"},
                    "metric_names": {"const": "intentionally_omitted"},
                    "full_view": {"const": "audit-deepeval --bindings PACK"},
                },
            },
        },
        "$defs": {
            name: deepcopy(definitions[name]) for name in ("token", "version", "hash", "population")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("deepeval_bound_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
