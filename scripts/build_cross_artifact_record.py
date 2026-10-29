"""Generate strict mapped cross-artifact capture from the review and record owners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _record_schema import schema_main
from _schema_compose import embed_contracts, nullable, object_schema, reference, tagged

from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES
from underwrite.instrument.ingest.cross_artifact import CORRESPONDENCE_MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/cross_artifact_record.v1.schema.json"
SCHEMA = "cross_artifact_record.v1"
MEMBERS = (
    "goppi-assessment.json",
    "goppi-result.json",
    "promptfoo.json",
    "correspondence.json",
)


def _at(path: str, value: dict[str, Any]) -> dict[str, Any]:
    for part in reversed(path.split("/")):
        value = {"properties": {part: value}}
    return value


def _state(path: str, state: str) -> dict[str, Any]:
    return _at(path + "/state", {"const": state})


def build_schema() -> dict[str, Any]:
    definitions, _ = embed_contracts(
        ROOT,
        ("cross_artifact_review", "evidence_error", "record_error", "record_verification"),
    )
    members = {
        "type": "array",
        "minItems": len(MEMBERS),
        "maxItems": len(MEMBERS),
        "prefixItems": [
            object_schema(
                {
                    "name": {"const": name},
                    "raw_hash": reference("record_verification__digest"),
                    "bytes": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": (
                            CORRESPONDENCE_MAX_BYTES
                            if name == "correspondence.json"
                            else DEFAULT_MAX_BYTES
                        ),
                    },
                }
            )
            for name in MEMBERS
        ],
        "items": False,
    }
    definitions["analysis"] = tagged(
        [
            object_schema(
                {
                    "state": {"const": "result"},
                    "report": {
                        "allOf": [
                            reference("cross_artifact_review"),
                            {
                                "properties": {
                                    "relation": {"const": "declared_case_mapping_descriptive"},
                                    "checks": {
                                        "properties": {
                                            "declaration_source_hashes": {"const": "matched"}
                                        }
                                    },
                                }
                            },
                        ]
                    },
                }
            ),
            object_schema({"state": {"const": "error"}, "error": reference("evidence_error")}),
        ]
    )
    identity = object_schema(
        {
            "record_id": reference("record_verification__record_id"),
            "manifest_digest": reference("record_verification__digest"),
        }
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
                                    "files_checked": {"const": 6},
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
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + SCHEMA,
        "title": SCHEMA,
        "description": (
            "Four captured inputs and a mapped descriptive cross-artifact review. "
            "Stored-byte integrity does not authenticate producers or authorize approval. "
            "Runtime owns equality between captured hashes and report identities, and "
            "between published and verified record identities."
        ),
        **object_schema(
            {
                "schema": {"const": SCHEMA},
                "command": {"const": "records review-artifacts"},
                "source": object_schema({"state": {"const": "captured"}, "members": members}),
                "analysis": reference("analysis"),
                "storage": reference("storage"),
                "exit_code": {"enum": [0, 2, 3]},
            }
        ),
        "$defs": definitions,
        "allOf": [
            {
                "if": _state("storage", "error"),
                "then": _at("exit_code", {"const": 3}),
                "else": {
                    "if": _state("analysis", "error"),
                    "then": _at("exit_code", {"const": 2}),
                    "else": _at("exit_code", {"const": 0}),
                },
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(schema_main(build_schema(), TARGET, __doc__))
