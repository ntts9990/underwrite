"""Card: record-reuse | Paper: current evidence capture boundary | Stage: storage-capture

Compose single-source capture envelopes from their accounting and storage owners.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

if __package__:
    from ._schema_compose import embed_contracts, nullable, object_schema, reference, tagged
else:
    from _schema_compose import embed_contracts, nullable, object_schema, reference, tagged

from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES


def _state(field: str, state: str) -> dict[str, Any]:
    return {"properties": {field: {"properties": {"state": {"const": state}}}}}


def build_record_schema(
    root: Path,
    *,
    accounting_owner: str,
    schema_name: str,
    command: str,
    description: str,
    accounting_version: str = "v1",
) -> dict[str, Any]:
    owners = (accounting_owner, "evidence_error", "record_error", "record_verification")
    definitions, _ = embed_contracts(root, owners, versions={accounting_owner: accounting_version})
    identity = object_schema(
        {
            "record_id": reference("record_verification__record_id"),
            "manifest_digest": reference("record_verification__digest"),
        }
    )
    definitions["source"] = tagged(
        [
            object_schema(
                {
                    "state": {"const": "captured"},
                    "raw_hash": reference("record_verification__digest"),
                    "bytes": {"type": "integer", "minimum": 0, "maximum": DEFAULT_MAX_BYTES},
                }
            ),
            object_schema({"state": {"const": "unavailable"}}),
        ]
    )
    definitions["analysis"] = tagged(
        [
            object_schema({"state": {"const": "result"}, "report": reference(accounting_owner)}),
            object_schema(
                {
                    "state": {"const": "error"},
                    "phase": {"enum": ["acquisition", "analysis"]},
                    "error": reference("evidence_error"),
                }
            ),
        ]
    )
    definitions["storage"] = tagged(
        [
            object_schema(
                {
                    "state": {"const": "saved"},
                    **identity["properties"],
                    "verification": {
                        "allOf": [
                            reference("record_verification"),
                            {
                                "properties": {
                                    "valid": {"const": True},
                                    "findings": {"maxItems": 0},
                                    "links_checked": {"const": 0},
                                    "files_checked": {"enum": [2, 3]},
                                }
                            },
                        ]
                    },
                }
            ),
            object_schema(
                {
                    "state": {"const": "error"},
                    "error": reference("record_error"),
                    "published_record": nullable(identity),
                }
            ),
        ]
    )
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + schema_name,
        "title": schema_name,
        "description": description,
        **object_schema(
            {
                "schema": {"const": schema_name},
                "command": {"const": command},
                "source": reference("source"),
                "analysis": reference("analysis"),
                "storage": reference("storage"),
                "exit_code": {"enum": [0, 2, 3]},
            }
        ),
        "$defs": definitions,
        "allOf": [
            {
                "if": _state("storage", "saved"),
                "then": {
                    "if": _state("source", "captured"),
                    "then": {
                        "properties": {
                            "storage": {
                                "properties": {
                                    "verification": {"properties": {"files_checked": {"const": 3}}}
                                }
                            }
                        }
                    },
                    "else": {
                        "properties": {
                            "storage": {
                                "properties": {
                                    "verification": {"properties": {"files_checked": {"const": 2}}}
                                }
                            }
                        }
                    },
                },
            },
            {
                "if": _state("storage", "error"),
                "then": {"properties": {"exit_code": {"const": 3}}},
                "else": {
                    "if": _state("analysis", "error"),
                    "then": {"properties": {"exit_code": {"const": 2}}},
                    "else": {"properties": {"exit_code": {"const": 0}}},
                },
            },
            {
                "if": _state("source", "unavailable"),
                "then": {
                    "properties": {
                        "analysis": {
                            "properties": {
                                "state": {"const": "error"},
                                "phase": {"const": "acquisition"},
                            },
                            "required": ["phase"],
                        }
                    }
                },
                "else": {
                    "if": _state("analysis", "error"),
                    "then": {
                        "properties": {"analysis": {"properties": {"phase": {"const": "analysis"}}}}
                    },
                },
            },
        ],
    }
    return schema


def schema_main(schema: dict[str, Any], target: Path, description: str | None) -> int:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not target.exists() or target.read_bytes() != raw:
            print(f"{target.name} is stale")
            return 1
    else:
        target.write_bytes(raw)
    return 0
