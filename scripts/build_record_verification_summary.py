"""Generate bounded stored-record verification from its full result contract."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/record_verification.v1.schema.json"
TARGET = ROOT / "contracts/record_verification_summary.v1.schema.json"


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    fields = cast(dict[str, Any], full["properties"])
    definitions = cast(dict[str, Any], full["$defs"])
    file_codes = cast(list[str], definitions["file_finding"]["properties"]["code"]["enum"])
    link_codes = cast(list[str], definitions["link_finding"]["properties"]["code"]["enum"])
    codes = [*file_codes, *link_codes]
    if len(codes) != len(set(codes)):
        raise ValueError("DUPLICATE_VERIFICATION_FINDING_CODE")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:record_verification_summary.v1",
        "title": "record_verification_summary.v1",
        "description": (
            "Content-minimized stored-byte and direct-link integrity result. "
            "Finding coordinates are omitted; valid does not establish provenance or approval."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "view", "record_id", "manifest_digest", "valid",
            "links_check_states", "file_check_states", "verification_scope", "limitations",
            "detail_state",
        ],
        "properties": {
            "schema": {"const": "record_verification_summary.v1"},
            "view": {"const": "summary"},
            **{
                name: deepcopy(fields[name])
                for name in (
                    "record_id", "manifest_digest", "valid", "verification_scope",
                )
            },
            "file_check_states": {
                "type": "array",
                "maxItems": fields["files_checked"]["maximum"],
                "items": {"enum": ["CHECKED_NO_FINDING", *file_codes]},
            },
            "links_check_states": {
                "type": "array",
                "maxItems": fields["links_checked"]["maximum"],
                "items": {"enum": ["CHECKED_NO_FINDING", *link_codes]},
            },
            "limitations": {
                "const": [
                    "SOURCE_PROVENANCE_NOT_ESTABLISHED",
                    "LINKED_RECORD_FILE_BYTES_NOT_VERIFIED",
                    "RERUN_NOT_PERFORMED",
                    "NO_APPROVAL_AUTHORITY",
                    "FINDING_COORDINATES_OMITTED",
                    "CHECK_STATES_NOT_COORDINATE_ALIGNED",
                ]
            },
            "detail_state": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file_names", "linked_record_ids", "full_view"],
                "properties": {
                    "file_names": {"const": "intentionally_omitted"},
                    "linked_record_ids": {"const": "intentionally_omitted"},
                    "full_view": {"const": "records verify --view full"},
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"valid": {"const": True}}},
                "then": {
                    "properties": {
                        "file_check_states": {"items": {"const": "CHECKED_NO_FINDING"}},
                        "links_check_states": {"items": {"const": "CHECKED_NO_FINDING"}},
                    }
                },
                "else": {
                    "anyOf": [
                        {
                            "properties": {
                                "file_check_states": {
                                    "contains": {"enum": file_codes}
                                }
                            }
                        },
                        {
                            "properties": {
                                "links_check_states": {
                                    "contains": {"enum": link_codes}
                                }
                            }
                        },
                    ]
                },
            }
        ],
        "$defs": {
            name: deepcopy(definitions[name]) for name in ("record_id", "digest")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("record_verification_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
