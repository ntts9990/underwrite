"""Derive bounded DeepEval accounting from the full two-profile contract."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/deepeval_accounting.v3.schema.json"
TARGET = ROOT / "contracts/deepeval_accounting_summary.v1.schema.json"


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    if set(full) != {"$schema", "$id", "title", "description", "if", "then", "else"}:
        raise ValueError("DEEPEVAL_ACCOUNTING_CONTRACT_CHANGED")
    branches = (cast(dict[str, Any], full["then"]), cast(dict[str, Any], full["else"]))
    first, second = branches
    required = cast(list[str], first["required"])
    if required != second["required"] or "cases" not in required:
        raise ValueError("DEEPEVAL_ACCOUNTING_FIELDS_CHANGED")
    common = (
        "schema", "source", "raw_hash", "scope", "declared_case_counts", "next_actions"
    )
    varying = ("rule_profile", "counts", "limitations", "reader_profile")
    fields = [cast(dict[str, Any], branch["properties"]) for branch in branches]
    if any(set(field) != set((*common, *varying, "cases")) for field in fields):
        raise ValueError("DEEPEVAL_ACCOUNTING_FIELDS_CHANGED")
    if any(fields[0][name] != fields[1][name] for name in common):
        raise ValueError("DEEPEVAL_COMMON_FIELD_DRIFT")
    properties = {name: deepcopy(fields[0][name]) for name in common}
    properties["schema"] = {"const": "deepeval_accounting_summary.v1"}
    properties.update({name: {} for name in varying})
    properties["view"] = {"const": "summary"}
    properties["detail_state"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["cases", "metric_names", "evaluator_errors", "full_view"],
        "properties": {
            "cases": {"const": "intentionally_omitted"},
            "metric_names": {"const": "intentionally_omitted"},
            "evaluator_errors": {"const": "intentionally_omitted"},
            "full_view": {"const": "audit-deepeval --view accounting"},
        },
    }
    return {
        "$schema": full["$schema"],
        "$id": "urn:underwrite:contract:deepeval_accounting_summary.v1",
        "title": "deepeval_accounting_summary.v1",
        "description": (
            "Content-minimized supplied DeepEval metric accounting. "
            "Case names, metric names and evaluator error text are omitted; "
            "planned and expectation populations remain unknown."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [name for name in required if name != "cases"] + ["view", "detail_state"],
        "properties": properties,
        "allOf": [
            {
                "if": deepcopy(full["if"]),
                "then": {
                    "properties": {name: deepcopy(fields[0][name]) for name in varying},
                    "allOf": deepcopy(first["allOf"]),
                },
                "else": {
                    "properties": {name: deepcopy(fields[1][name]) for name in varying},
                    "allOf": deepcopy(second["allOf"]),
                },
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("deepeval_accounting_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
