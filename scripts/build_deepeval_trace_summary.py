"""Generate coordinate-free DeepEval trace summary from the trace-index owner."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from underwrite.cli.audit_deepeval import TRACE_SUMMARY_LINKABILITY

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/deepeval_trace_index.v2.schema.json"
TARGET = ROOT / "contracts/deepeval_trace_summary.v1.schema.json"


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    fields = deepcopy(cast(dict[str, Any], full["properties"]))
    required = list(cast(list[str], full["required"]))
    if "cases" not in fields or "cases" not in required:
        raise ValueError("DEEPEVAL_TRACE_CASE_DETAIL_MISSING")
    fields.pop("cases")
    required.remove("cases")
    limitations = cast(list[str], fields["limitations"]["const"])
    full_linkability, summary_linkability = TRACE_SUMMARY_LINKABILITY
    if limitations.count(full_linkability) != 1:
        raise ValueError("DEEPEVAL_TRACE_LINKABILITY_CHANGED")
    fields["limitations"]["const"] = [
        summary_linkability if item == full_linkability else item for item in limitations
    ]
    fields["schema"] = {"const": "deepeval_trace_summary.v1"}
    fields["view"] = {"const": "trace-summary"}
    fields["detail_state"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["case_coordinates", "span_coordinates", "parent_pointers", "full_view"],
        "properties": {
            "case_coordinates": {"const": "intentionally_omitted"},
            "span_coordinates": {"const": "intentionally_omitted"},
            "parent_pointers": {"const": "intentionally_omitted"},
            "full_view": {"const": "audit-deepeval --view trace-index"},
        },
    }
    required.extend(("view", "detail_state"))
    return {
        "$schema": full["$schema"],
        "$id": "urn:underwrite:contract:deepeval_trace_summary.v1",
        "title": "deepeval_trace_summary.v1",
        "description": (
            "Content-minimized source-claimed DeepEval trace and span populations. "
            "Coordinates and parent pointers are omitted; links do not establish causality."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("deepeval_trace_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
