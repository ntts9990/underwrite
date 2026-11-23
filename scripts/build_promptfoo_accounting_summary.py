"""Generate bounded Promptfoo accounting summary from the actions contract."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from underwrite.instrument.evidence.promptfoo_compat import ACTION_BY_FINDING

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/promptfoo_accounting_actions.v1.schema.json"
TARGET = ROOT / "contracts/promptfoo_accounting_summary.v1.schema.json"


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    fields = cast(dict[str, Any], full["properties"])
    definitions = cast(dict[str, Any], full["$defs"])
    maximum = cast(int, fields["findings"]["maxItems"])
    codes = cast(list[str], definitions["finding"]["properties"]["code"]["enum"])
    actions = dict(ACTION_BY_FINDING)
    if (
        len(actions) != len(ACTION_BY_FINDING)
        or set(codes) != set(actions)
        or set(actions.values()) != set(fields["next_actions"]["items"]["enum"])
    ):
        raise ValueError("PROMPTFOO_FINDING_CODES_CHANGED")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:promptfoo_accounting_summary.v1",
        "title": "promptfoo_accounting_summary.v1",
        "description": (
            "Content-minimized Promptfoo row-count accounting with owner action codes. "
            "Finding coordinates and source text are omitted."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "view", "source", "raw_hash", "reader_profile", "scope",
            "producer_stats", "row_tallies", "deltas", "declared_relation",
            "finding_counts", "limitations", "next_actions", "source_binding",
            "detail_state",
        ],
        "properties": {
            "schema": {"const": "promptfoo_accounting_summary.v1"},
            "view": {"const": "summary"},
            **{
                name: deepcopy(fields[name])
                for name in (
                    "source", "raw_hash", "reader_profile", "scope", "producer_stats",
                    "row_tallies", "deltas", "declared_relation", "limitations", "next_actions",
                )
            },
            "finding_counts": {
                "type": "object",
                "additionalProperties": False,
                "required": codes,
                "properties": {
                    code: {"type": "integer", "minimum": 0, "maximum": maximum}
                    for code in codes
                },
            },
            "source_binding": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "manifest_hash", "expected_hash_check"],
                "properties": {
                    "kind": {"enum": ["direct_file", "manifest_case"]},
                    "manifest_hash": {"type": ["string", "null"]},
                    "expected_hash_check": {
                        "enum": ["not_applicable", "not_supplied", "matched"]
                    },
                },
                "oneOf": [
                    {
                        "properties": {
                            "kind": {"const": "direct_file"},
                            "manifest_hash": {"type": "null"},
                            "expected_hash_check": {"const": "not_applicable"},
                        }
                    },
                    {
                        "properties": {
                            "kind": {"const": "manifest_case"},
                            "manifest_hash": {"$ref": "#/$defs/hash"},
                            "expected_hash_check": {"enum": ["not_supplied", "matched"]},
                        }
                    },
                ],
            },
            "detail_state": {
                "type": "object",
                "additionalProperties": False,
                "required": ["row_coordinates", "source_text", "full_view"],
                "properties": {
                    "row_coordinates": {"const": "intentionally_omitted"},
                    "source_text": {"const": "intentionally_omitted"},
                    "full_view": {
                        "enum": [
                            "audit-promptfoo --view actions",
                            "audit-counts --view full",
                        ]
                    },
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"finding_counts": {"properties": {code: {"minimum": 1}}}}},
                "then": {
                    "properties": {"next_actions": {"contains": {"const": action}}}
                },
                "else": {
                    "properties": {"next_actions": {"not": {"contains": {"const": action}}}}
                },
            }
            for code, action in actions.items()
        ]
        + [
            {
                "if": {
                    "properties": {
                        "source_binding": {"properties": {"kind": {"const": "direct_file"}}}
                    }
                },
                "then": {
                    "properties": {
                        "detail_state": {
                            "properties": {
                                "full_view": {"const": "audit-promptfoo --view actions"}
                            }
                        }
                    }
                },
                "else": {
                    "properties": {
                        "detail_state": {
                            "properties": {"full_view": {"const": "audit-counts --view full"}}
                        }
                    }
                },
            }
        ],
        "$defs": {
            name: deepcopy(definitions[name])
            for name in ("hash", "counts", "deltas", "nonnegative", "signed")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("promptfoo_accounting_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
