"""Derive bounded Langfuse accounting from both supported reader contracts."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from underwrite.cli.audit_langfuse import SUMMARY_LINKABILITY

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/langfuse_accounting.v2.schema.json"
TARGET = ROOT / "contracts/langfuse_accounting_summary.v1.schema.json"


def _branch(source: dict[str, Any]) -> dict[str, Any]:
    branch = deepcopy(source)
    properties = cast(dict[str, Any], branch["properties"])
    required = cast(list[str], branch["required"])
    for name in ("observations", "scores"):
        if name not in properties or name not in required:
            raise ValueError("LANGFUSE_ROW_DETAIL_MISSING")
        properties.pop(name)
        required.remove(name)
    old, new = SUMMARY_LINKABILITY
    limitations = cast(list[str], properties["limitations"]["const"])
    if limitations.count(old) != 1:
        raise ValueError("LANGFUSE_LINKABILITY_CHANGED")
    properties["limitations"]["const"] = [new if item == old else item for item in limitations]
    properties["schema"] = {"const": "langfuse_accounting_summary.v1"}
    properties["view"] = {"const": "summary"}
    properties["detail_state"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["observation_rows", "score_rows", "row_patterns", "full_view"],
        "properties": {
            "observation_rows": {"const": "intentionally_omitted"},
            "score_rows": {"const": "intentionally_omitted"},
            "row_patterns": {"const": "intentionally_omitted"},
            "full_view": {"const": "audit-langfuse --view accounting"},
        },
    }
    required.extend(("view", "detail_state"))
    return branch


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    if set(full) != {"$schema", "$id", "title", "description", "if", "then", "else"}:
        raise ValueError("LANGFUSE_ACCOUNTING_CONTRACT_CHANGED")
    return {
        "$schema": full["$schema"],
        "$id": "urn:underwrite:contract:langfuse_accounting_summary.v1",
        "title": "langfuse_accounting_summary.v1",
        "description": (
            "Content-minimized selected Langfuse score kinds and local associations. "
            "Observation and score rows are omitted; collection windows and "
            "completeness remain unknown."
        ),
        "if": deepcopy(full["if"]),
        "then": _branch(cast(dict[str, Any], full["then"])),
        "else": _branch(cast(dict[str, Any], full["else"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("langfuse_accounting_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
