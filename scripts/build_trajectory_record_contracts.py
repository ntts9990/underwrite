"""Compose retained trajectory outputs from analysis, history and storage contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _schema_compose import embed_contracts, nullable, object_schema, reference, tagged

from underwrite.instrument.evidence.goppi_trajectory import MEMBERS
from underwrite.instrument.ingest.goppi_trajectory import HISTORY_SCHEMA, trajectory_member_limits

ROOT = Path(__file__).resolve().parents[1]


def build_history() -> dict[str, Any]:
    digest = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
    members = {
        "type": "array",
        "minItems": len(MEMBERS),
        "maxItems": len(MEMBERS),
        "items": object_schema(
            {
                "name": {"enum": list(MEMBERS)},
                "raw_hash": digest,
                "bytes": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": max(trajectory_member_limits().values()),
                },
            }
        ),
    }
    common = {
        "schema": {"const": HISTORY_SCHEMA},
        "source_members": reference("source_members"),
        "source_authentication_independently_verified": {"const": False},
        "producer_projection_independently_verified": {"const": False},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + HISTORY_SCHEMA,
        **tagged(
            [
                object_schema(
                    {
                        **common,
                        "state": {"const": state},
                        "integrity_scope": {"const": integrity},
                        "authenticity": {"const": authenticity},
                    }
                )
                for state, integrity, authenticity in (
                    ("not_performed", None, None),
                    ("verified", "self_consistent_content_closure", "content_digest_only"),
                )
            ]
        ),
        "$defs": {"source_members": members},
    }


def build_envelopes(root: Path) -> dict[str, dict[str, Any]]:
    definitions, _ = embed_contracts(
        root,
        (
            "goppi_trajectory_review",
            "trajectory_verification_history",
            "evidence_error",
            "record_error",
            "record_verification",
        ),
    )
    definitions["analysis"] = tagged(
        [
            object_schema(
                {"state": {"const": "result"}, "report": reference("goppi_trajectory_review")}
            ),
            object_schema({"state": {"const": "error"}, "error": reference("evidence_error")}),
        ]
    )
    definitions["source"] = object_schema(
        {
            "state": {"const": "captured"},
            "source_members": reference("trajectory_verification_history__source_members"),
        }
    )
    identity = {
        "record_id": reference("record_verification__record_id"),
        "manifest_digest": reference("record_verification__digest"),
    }
    verification = {
        "allOf": [
            reference("record_verification"),
            {
                "properties": {
                    "valid": {"const": True},
                    "files_checked": {"const": len(MEMBERS) + 3},
                    "links_checked": {"const": 0},
                    "findings": {"maxItems": 0},
                }
            },
        ]
    }
    definitions["storage"] = tagged(
        [
            object_schema({"state": {"const": "saved"}, **identity, "verification": verification}),
            object_schema(
                {
                    "state": {"const": "error"},
                    "error": reference("record_error"),
                    "published_record": nullable(object_schema(identity)),
                }
            ),
        ]
    )
    shared = {
        "source": reference("source"),
        "analysis": reference("analysis"),
        "native_verification_history": reference("trajectory_verification_history"),
    }
    schemas: dict[str, dict[str, Any]] = {}
    for name, fields in (
        (
            "trajectory_record.v1",
            {
                "command": {"const": "records audit-trajectory"},
                **shared,
                "storage": reference("storage"),
                "exit_code": {"enum": [0, 2, 3]},
            },
        ),
        (
            "trajectory_record_read.v1",
            {
                "command": {"const": "records read-trajectory"},
                **identity,
                **shared,
                "current_storage_verification": verification,
                "native_verification_performed_now": {"const": False},
            },
        ),
    ):
        schemas[name] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:underwrite:contract:" + name,
            **object_schema({"schema": {"const": name}, **fields}),
            "$defs": definitions,
        }
    schemas["trajectory_record.v1"]["allOf"] = [
        {
            "if": {"properties": {"storage": {"properties": {"state": {"const": "error"}}}}},
            "then": {"properties": {"exit_code": {"const": 3}}},
            "else": {
                "if": {"properties": {"analysis": {"properties": {"state": {"const": "error"}}}}},
                "then": {"properties": {"exit_code": {"const": 2}}},
                "else": {"properties": {"exit_code": {"const": 0}}},
            },
        }
    ]
    return schemas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    schemas = {HISTORY_SCHEMA: build_history()}
    history_path = ROOT / "contracts" / (HISTORY_SCHEMA + ".schema.json")
    history_raw = json.dumps(schemas[HISTORY_SCHEMA], ensure_ascii=False, indent=2) + "\n"
    if not args.check:
        history_path.write_text(history_raw)
    schemas.update(build_envelopes(ROOT))
    for name, schema in schemas.items():
        path = ROOT / "contracts" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if path.read_text() != raw:
                raise SystemExit("STALE_TRAJECTORY_RECORD_CONTRACT")
        else:
            path.write_text(raw)


if __name__ == "__main__":
    main()
