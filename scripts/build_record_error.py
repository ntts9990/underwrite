"""Card: record-reuse | Paper: record_error.v1 | Stage: storage-read.

Generate the finite record diagnostic contract from its shared vocabulary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from underwrite.error_contract.registry import RECORD_EXIT_CODE, RECORD_PROJECTIONS

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/record_error.v1.schema.json"


def build_schema() -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:record_error.v1",
        "title": "record_error.v1",
        "description": "Finite records command failure without caller-supplied diagnostics.",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema",
            "code",
            "reason",
            "location",
            "next_action",
            "retryable",
            "exit_code",
        ],
        "properties": {
            "schema": {"const": "record_error.v1"},
            "code": {"type": "string"},
            "reason": {"type": "string"},
            "location": {
                "type": "string",
                "maxLength": 128,
                "pattern": (
                    "^(?:|/(?:root|store|record_id|spec(?:/(?:files|links)"
                    "(?:/[0-9]+(?:/[a-z_]+)?)?)?|source_base|record|catalog|verification|output))"
                    "(?![\\s\\S])"
                ),
            },
            "next_action": {"type": "string"},
            "retryable": {"const": False},
            "exit_code": {"const": RECORD_EXIT_CODE},
        },
    }
    schema["oneOf"] = [{"$ref": "#/$defs/" + code} for code in RECORD_PROJECTIONS]
    schema["$defs"] = {
        code: {
            "properties": {
                "code": {"const": code},
                "reason": {"const": projection.reason},
                "next_action": {"const": projection.next_action},
            }
        }
        for code, projection in RECORD_PROJECTIONS.items()
    }
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("record_error.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
