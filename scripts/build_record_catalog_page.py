"""Generate the bounded record catalog page from its full output contract."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from underwrite.cli.records import CATALOG_PAGE_SIZE as PAGE_SIZE

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/record_catalog.v1.schema.json"
TARGET = ROOT / "contracts/record_catalog_page.v1.schema.json"


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    fields = cast(dict[str, Any], full["properties"])
    definitions = deepcopy(cast(dict[str, Any], full["$defs"]))
    complete = cast(dict[str, Any], definitions["complete"])
    complete["required"] = [
        name for name in complete["required"] if name not in ("label", "captured_at")
    ]
    del complete["properties"]["label"]
    del complete["properties"]["captured_at"]
    del definitions["label"]
    del definitions["captured_at"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:record_catalog_page.v1",
        "title": "record_catalog_page.v1",
        "description": (
            "One bounded observation of a local record catalog. Continuations bind to the "
            "observed digest; this is neither a transactional snapshot nor file verification."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "view", "catalog_state", "catalog_digest", "record_count",
            "offset", "page_size", "records", "next_page", "restart_offset",
            "next_action", "verification_scope", "limitations", "detail_state",
        ],
        "properties": {
            "schema": {"const": "record_catalog_page.v1"},
            "view": {"const": "page"},
            "catalog_state": {"enum": ["observed", "changed"]},
            "catalog_digest": {"$ref": "#/$defs/digest"},
            "record_count": deepcopy(fields["record_count"]),
            "offset": {
                "type": "integer", "minimum": 0,
                "maximum": fields["record_count"]["maximum"],
                "multipleOf": PAGE_SIZE,
            },
            "page_size": {"const": PAGE_SIZE},
            "records": {
                "type": "array",
                "maxItems": PAGE_SIZE,
                "items": deepcopy(fields["records"]["items"]),
                "uniqueItems": deepcopy(fields["records"]["uniqueItems"]),
            },
            "next_page": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["offset"],
                        "properties": {
                            "offset": {
                                "type": "integer", "minimum": PAGE_SIZE,
                                "maximum": fields["record_count"]["maximum"],
                                "multipleOf": PAGE_SIZE,
                            },
                        },
                    },
                ]
            },
            "restart_offset": {"oneOf": [{"type": "null"}, {"const": 0}]},
            "next_action": {
                "enum": [
                    "FOLLOW_NEXT_PAGE_OR_SELECT_ID",
                    "SELECT_ID_OR_FINISH",
                    "NO_RECORDS",
                    "RESTART_FROM_FIRST_PAGE",
                ]
            },
            "verification_scope": {"const": "catalog_metadata_only"},
            "limitations": {
                "const": [
                    "FILES_NOT_VERIFIED",
                    "SOURCE_PROVENANCE_NOT_ESTABLISHED",
                    "CATALOG_NOT_ATOMIC_SNAPSHOT",
                ]
            },
            "detail_state": {
                "type": "object",
                "additionalProperties": False,
                "required": ["labels", "captured_at", "full_view"],
                "properties": {
                    "labels": {"const": "intentionally_omitted"},
                    "captured_at": {"const": "intentionally_omitted"},
                    "full_view": {"const": "records list --view full"},
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"catalog_state": {"const": "changed"}}},
                "then": {
                    "properties": {
                        "records": {"maxItems": 0},
                        "next_page": {"type": "null"},
                        "restart_offset": {"const": 0},
                        "next_action": {"const": "RESTART_FROM_FIRST_PAGE"},
                    }
                },
                "else": {
                    "properties": {
                        "restart_offset": {"type": "null"},
                        "next_action": {
                            "enum": [
                                "FOLLOW_NEXT_PAGE_OR_SELECT_ID",
                                "SELECT_ID_OR_FINISH",
                                "NO_RECORDS",
                            ]
                        },
                    }
                },
            },
            {
                "if": {"properties": {"next_action": {"const": "FOLLOW_NEXT_PAGE_OR_SELECT_ID"}}},
                "then": {
                    "properties": {
                        "records": {"minItems": PAGE_SIZE},
                        "next_page": {"type": "object"},
                    },
                    "oneOf": [
                        {
                            "properties": {
                                "offset": {"const": offset},
                                "record_count": {"minimum": offset + PAGE_SIZE + 1},
                                "next_page": {
                                    "properties": {"offset": {"const": offset + PAGE_SIZE}}
                                },
                            }
                        }
                        for offset in range(
                            0, fields["record_count"]["maximum"] - PAGE_SIZE, PAGE_SIZE
                        )
                    ],
                },
                "else": {
                    "if": {
                        "properties": {
                            "next_action": {"enum": ["SELECT_ID_OR_FINISH", "NO_RECORDS"]}
                        }
                    },
                    "then": {"properties": {"next_page": {"type": "null"}}},
                },
            },
            {
                "if": {"properties": {"next_action": {"const": "SELECT_ID_OR_FINISH"}}},
                "then": {
                    "properties": {"records": {"minItems": 1}},
                    "oneOf": [
                        {
                            "properties": {
                                "offset": {"const": offset},
                                "record_count": {
                                    "minimum": offset + 1,
                                    "maximum": min(
                                        offset + PAGE_SIZE, fields["record_count"]["maximum"]
                                    ),
                                },
                            }
                        }
                        for offset in range(0, fields["record_count"]["maximum"], PAGE_SIZE)
                    ],
                },
            },
            {
                "if": {"properties": {"next_action": {"const": "NO_RECORDS"}}},
                "then": {
                    "properties": {
                        "record_count": {"const": 0},
                        "offset": {"const": 0},
                        "records": {"maxItems": 0},
                    }
                },
            },
            {
                "if": {
                    "properties": {
                        "catalog_state": {"const": "observed"},
                        "record_count": {"const": 0},
                    }
                },
                "then": {"properties": {"next_action": {"const": "NO_RECORDS"}}},
            },
        ],
        "$defs": definitions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("record_catalog_page.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
