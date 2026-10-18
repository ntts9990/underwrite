"""Card: record-reuse | Paper: langfuse_record.v1 | Stage: storage-capture

Generate the fixed Langfuse capture envelope from existing owner contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _schema_compose import embed_contracts, nullable, object_schema, reference, tagged

from underwrite.cli.record_langfuse import CLI_RECORD_SCHEMA, SCHEMA
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / f"contracts/{SCHEMA}.schema.json"
CLI_TARGET = ROOT / f"contracts/{CLI_RECORD_SCHEMA}.schema.json"
OWNERS = ("langfuse_accounting", "evidence_error", "record_error", "record_verification")


def _state(field: str, state: str) -> dict[str, Any]:
    return {"properties": {field: {"properties": {"state": {"const": state}}}}}


def _source_states(observations: bool, scores: bool) -> dict[str, Any]:
    return {
        "properties": {
            "sources": {
                "properties": {
                    role: {
                        "properties": {
                            "state": {"const": "captured" if captured else "unavailable"}
                        }
                    }
                    for role, captured in (("observations", observations), ("scores", scores))
                }
            }
        }
    }


def _build_schema(record_name: str, accounting_owner: str) -> dict[str, Any]:
    definitions, _ = embed_contracts(ROOT, (accounting_owner, *OWNERS[1:]))
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
            object_schema(
                {"state": {"const": "unavailable"}, "error": reference("evidence_error")}
            ),
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
                                    "files_checked": {"enum": [2, 3, 4]},
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
        "$id": f"urn:underwrite:contract:{record_name}",
        "title": record_name,
        "description": (
            "Independent Langfuse "
            + ("CLI accounting" if record_name == CLI_RECORD_SCHEMA else "accounting")
            + " and private capture outcomes. "
            "A saved record verifies stored bytes, not producer execution or approval. "
            "Null published_record means no identity was returned, not proven absence."
        ),
        **object_schema(
            {
                "schema": {"const": record_name},
                "command": {"const": "records audit-langfuse"},
                "sources": object_schema(
                    {"observations": reference("source"), "scores": reference("source")}
                ),
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
                    "allOf": [
                        {
                            "if": _source_states(observations, scores),
                            "then": {
                                "properties": {
                                    "storage": {
                                        "properties": {
                                            "verification": {
                                                "properties": {
                                                    "files_checked": {
                                                        "const": 2 + observations + scores
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            },
                        }
                        for observations in (False, True)
                        for scores in (False, True)
                    ]
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
                "if": _source_states(True, True),
                "then": {
                    "if": _state("analysis", "error"),
                    "then": {
                        "properties": {"analysis": {"properties": {"phase": {"const": "analysis"}}}}
                    },
                },
                "else": {
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
            },
        ],
    }
    return schema


def build_schema() -> dict[str, Any]:
    """Keep the existing blob record contract and its generated bytes stable."""
    return _build_schema(SCHEMA, OWNERS[0])


def build_cli_schema() -> dict[str, Any]:
    """Embed CLI accounting into the same concrete two-source record envelope."""
    return _build_schema(CLI_RECORD_SCHEMA, "langfuse_cli_accounting")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    targets = ((TARGET, build_schema()), (CLI_TARGET, build_cli_schema()))
    for target, schema in targets:
        raw = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not target.exists() or target.read_bytes() != raw:
                print(f"{target.name} is stale")
                return 1
        else:
            target.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
