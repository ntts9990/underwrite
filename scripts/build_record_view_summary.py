"""Generate a content-minimized record view from its authored full contract."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from underwrite.cli.records import RECORD_FILE_PAGE_SIZE as PAGE_SIZE
from underwrite.service.records import MAX_TOTAL_BYTES

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/record_view.v1.schema.json"
TARGET = ROOT / "contracts/record_view_summary.v1.schema.json"


def _counts(names: list[str], maximum: int) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": names,
        "properties": {
            name: {"type": "integer", "minimum": 0, "maximum": maximum}
            for name in names
        },
    }


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    definitions = cast(dict[str, Any], full["$defs"])
    manifest = cast(dict[str, Any], definitions["result_record"]["properties"])
    file_count_max = cast(int, manifest["files"]["maxItems"])
    link_count_max = cast(int, manifest["links"]["maxItems"])
    excluded_max = cast(int, definitions["selection"]["properties"]["excluded"]["maxItems"])
    file_max_bytes = cast(int, definitions["stored_file"]["properties"]["bytes"]["maximum"])
    roles = cast(list[str], definitions["role"]["enum"])
    relations = cast(list[str], definitions["link"]["properties"]["relation"]["enum"])
    stored_file = cast(dict[str, Any], definitions["stored_file"]["properties"])
    verification_statuses = [
        option["properties"]["status"]["const"]
        for option in definitions["rerun_verification"]["oneOf"]
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:record_view_summary.v1",
        "title": "record_view_summary.v1",
        "description": (
            "Content-minimized manifest metadata for one published local record. "
            "Stored file bytes and linked records are not verified."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "view", "manifest_state", "manifest_digest", "record_id", "kind",
            "captured_at",
            "selection", "rerun_verification_status", "provenance_verified",
            "attestation_reference_state", "file_count", "file_role_counts",
            "declared_file_bytes", "file_offset", "file_page_size", "files",
            "next_file_page", "restart_offset", "next_action", "link_relation_counts",
            "verification_scope", "limitations", "detail_state",
        ],
        "properties": {
            "schema": {"const": "record_view_summary.v1"},
            "view": {"const": "summary"},
            "manifest_state": {"enum": ["observed", "changed"]},
            "manifest_digest": deepcopy(full["properties"]["manifest_digest"]),
            "record_id": deepcopy(manifest["record_id"]),
            "kind": deepcopy(manifest["kind"]),
            "captured_at": deepcopy(manifest["captured_at"]),
            "selection": {
                "type": "object",
                "additionalProperties": False,
                "required": ["scope", "excluded_state", "excluded_count"],
                "properties": {
                    "scope": deepcopy(definitions["selection"]["properties"]["scope"]),
                    "excluded_state": {"enum": ["not_declared", "declared"]},
                    "excluded_count": {
                        "oneOf": [
                            {"type": "null"},
                            {
                                "type": "integer", "minimum": 0,
                                "maximum": excluded_max,
                            },
                        ]
                    },
                },
            },
            "rerun_verification_status": {"enum": verification_statuses},
            "provenance_verified": deepcopy(definitions["provenance"]["properties"]["verified"]),
            "attestation_reference_state": {"enum": ["absent", "declared_unverified"]},
            "file_count": {
                "type": "integer", "minimum": manifest["files"]["minItems"],
                "maximum": file_count_max,
            },
            "file_role_counts": _counts(roles, file_count_max),
            "declared_file_bytes": {
                "type": "integer", "minimum": 0,
                "maximum": min(file_count_max * file_max_bytes, MAX_TOTAL_BYTES),
            },
            "file_offset": {
                "type": "integer", "minimum": 0,
                "maximum": file_count_max - PAGE_SIZE,
                "multipleOf": PAGE_SIZE,
            },
            "file_page_size": {"const": PAGE_SIZE},
            "files": {
                "type": "array",
                "maxItems": PAGE_SIZE,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "role", "media_type", "declared_sha256", "declared_bytes"],
                    "properties": {
                        "name": deepcopy(stored_file["name"]),
                        "role": deepcopy(stored_file["role"]),
                        "media_type": deepcopy(stored_file["media_type"]),
                        "declared_sha256": deepcopy(stored_file["sha256"]),
                        "declared_bytes": deepcopy(stored_file["bytes"]),
                    },
                },
                "uniqueItems": deepcopy(manifest["files"]["uniqueItems"]),
            },
            "next_file_page": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["offset"],
                        "properties": {
                            "offset": {
                                "type": "integer", "minimum": PAGE_SIZE,
                                "maximum": file_count_max - PAGE_SIZE,
                                "multipleOf": PAGE_SIZE,
                            }
                        },
                    },
                ]
            },
            "restart_offset": {"oneOf": [{"type": "null"}, {"const": 0}]},
            "next_action": {
                "enum": [
                    "FOLLOW_NEXT_FILE_PAGE_OR_SELECT_NAME",
                    "SELECT_NAME_OR_FINISH",
                    "RESTART_FROM_FIRST_FILE_PAGE",
                ]
            },
            "link_relation_counts": _counts(relations, link_count_max),
            "verification_scope": {"const": "canonical_published_manifest_only"},
            "limitations": {
                "const": [
                    "STORED_FILE_BYTES_NOT_READ_OR_VERIFIED",
                    "DIRECT_LINK_TARGETS_NOT_READ_OR_VERIFIED",
                    "SOURCE_PROVENANCE_NOT_ESTABLISHED",
                    "RERUN_REFERENCE_NOT_EXECUTED_NOW",
                    "NO_APPROVAL_AUTHORITY",
                ]
            },
            "detail_state": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "label", "metadata", "source_locators", "link_targets",
                    "artifact_references", "full_view",
                ],
                "properties": {
                    **{
                        name: {"const": "intentionally_omitted"}
                        for name in (
                            "label", "metadata", "source_locators", "link_targets",
                            "artifact_references",
                        )
                    },
                    "full_view": {"const": "records show --view full"},
                },
            },
        },
        "allOf": [
            {
                "if": {
                    "properties": {
                        "selection": {
                            "properties": {"excluded_state": {"const": "not_declared"}}
                        }
                    }
                },
                "then": {
                    "properties": {
                        "selection": {"properties": {"excluded_count": {"type": "null"}}}
                    }
                },
                "else": {
                    "properties": {
                        "selection": {"properties": {"excluded_count": {"type": "integer"}}}
                    }
                },
            },
            {
                "if": {"properties": {"manifest_state": {"const": "changed"}}},
                "then": {
                    "properties": {
                        "files": {"maxItems": 0},
                        "next_file_page": {"type": "null"},
                        "restart_offset": {"const": 0},
                        "next_action": {"const": "RESTART_FROM_FIRST_FILE_PAGE"},
                    }
                },
                "else": {
                    "properties": {
                        "files": {"minItems": 1},
                        "restart_offset": {"type": "null"},
                        "next_action": {
                            "enum": [
                                "FOLLOW_NEXT_FILE_PAGE_OR_SELECT_NAME",
                                "SELECT_NAME_OR_FINISH",
                            ]
                        },
                    }
                },
            },
            {
                "if": {
                    "properties": {
                        "next_action": {"const": "FOLLOW_NEXT_FILE_PAGE_OR_SELECT_NAME"}
                    }
                },
                "then": {
                    "properties": {
                        "files": {"minItems": PAGE_SIZE},
                        "next_file_page": {"type": "object"},
                    },
                    "oneOf": [
                        {
                            "properties": {
                                "file_offset": {"const": offset},
                                "file_count": {"minimum": offset + PAGE_SIZE + 1},
                                "next_file_page": {
                                    "properties": {"offset": {"const": offset + PAGE_SIZE}}
                                },
                            }
                        }
                        for offset in range(0, file_count_max - PAGE_SIZE, PAGE_SIZE)
                    ],
                },
            },
            {
                "if": {"properties": {"next_action": {"const": "SELECT_NAME_OR_FINISH"}}},
                "then": {
                    "properties": {
                        "files": {"minItems": 1},
                        "next_file_page": {"type": "null"},
                    },
                    "oneOf": [
                        {
                            "properties": {
                                "file_offset": {"const": offset},
                                "file_count": {
                                    "minimum": offset + 1,
                                    "maximum": offset + PAGE_SIZE,
                                },
                            }
                        }
                        for offset in range(0, file_count_max, PAGE_SIZE)
                    ],
                },
            },
        ],
        "$defs": {
            name: deepcopy(definitions[name])
            for name in ("record_id", "kind", "digest", "logical_name", "role", "media_type")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("record_view_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
