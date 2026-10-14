"""Derive Promptfoo's ordinal view contract from accounting's existing fields."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from underwrite.instrument.evidence.accounting import (
    CATEGORIES,
    REASON_ASSERT,
    REASON_ERROR,
    REASON_NONE,
    ROW_INDEX_LIMITATIONS,
    ROW_INDEX_SCOPE,
    ROW_LIMIT,
)

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTING = ROOT / "contracts/promptfoo_accounting.v1.schema.json"
TARGET = ROOT / "contracts/promptfoo_row_index.v1.schema.json"


def _object(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def build_schema() -> dict[str, object]:
    accounting = cast(dict[str, Any], json.loads(ACCOUNTING.read_text(encoding="utf-8")))
    fields = cast(dict[str, Any], accounting["properties"])
    definitions = cast(dict[str, Any], accounting["$defs"])
    row = _object(
        {
            "row_index": {"type": "integer", "minimum": 0, "maximum": ROW_LIMIT - 1},
            "category": {"enum": list(CATEGORIES)},
            "source_success": {"type": "boolean"},
            "source_failure_reason": {"enum": [REASON_NONE, REASON_ASSERT, REASON_ERROR]},
            "grading_pass": {"type": ["boolean", "null"]},
        }
    )
    row["description"] = (
        "Category is underwrite's source-flag accounting class, not a verified task outcome. "
        "Null grading_pass covers both absent and explicit-null gradingResult."
    )
    row["allOf"] = [
        {
            "if": {"properties": {"source_success": {"const": True}}},
            "then": {"properties": {"category": {"const": "successes"}}},
            "else": {
                "if": {"properties": {"source_failure_reason": {"const": REASON_ERROR}}},
                "then": {"properties": {"category": {"const": "errors"}}},
                "else": {"properties": {"category": {"const": "failures"}}},
            },
        }
    ]
    result = _object(
        {
            "schema": {"const": "promptfoo_row_index.v1"},
            "source": deepcopy(fields["source"]),
            "raw_hash": deepcopy(fields["raw_hash"]),
            "scope": _object({key: {"const": value} for key, value in ROW_INDEX_SCOPE.items()}),
            "producer_stats": deepcopy(fields["producer_stats"]),
            "row_tallies": deepcopy(fields["row_tallies"]),
            "deltas": deepcopy(fields["deltas"]),
            "declared_relation": deepcopy(fields["declared_relation"]),
            "findings": deepcopy(fields["findings"]),
            "rows": {"type": "array", "maxItems": ROW_LIMIT, "items": row},
            "verification": {"const": []},
            "limitations": {"const": list(ROW_INDEX_LIMITATIONS)},
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:promptfoo_row_index.v1",
        "title": "promptfoo_row_index.v1",
        "description": "Content-minimized ordinal triage of supplied Promptfoo source-flag rows.",
        **result,
        "$defs": {
            key: deepcopy(definitions[key])
            for key in ("hash", "counts", "deltas", "nonnegative", "signed", "finding")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        return 0 if TARGET.exists() and TARGET.read_text(encoding="utf-8") == rendered else 1
    TARGET.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
