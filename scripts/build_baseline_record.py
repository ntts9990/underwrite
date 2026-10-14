"""Generate the native baseline capture envelope from evidence and record owners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _record_schema import schema_main
from _schema_compose import embed_contracts, nullable, object_schema, reference, tagged

from underwrite.instrument.evidence.goppi_baseline import FILES
from underwrite.instrument.ingest.goppi_baseline import DOCUMENT_MAX_BYTES, ROWS_MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/baseline_record.v1.schema.json"


def _state(field: str, state: str) -> dict[str, Any]:
    return {"properties": {field: {"properties": {"state": {"const": state}}}}}


def build_schema() -> dict[str, Any]:
    definitions, _ = embed_contracts(
        ROOT, ("evidence_result", "evidence_error", "record_error", "record_verification")
    )
    identity = object_schema(
        {
            "record_id": reference("record_verification__record_id"),
            "manifest_digest": reference("record_verification__digest"),
        }
    )
    files = {
        "type": "array",
        "minItems": len(FILES),
        "maxItems": len(FILES),
        "prefixItems": [
            object_schema(
                {
                    "name": {"const": name},
                    "raw_hash": reference("record_verification__digest"),
                    "bytes": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": ROWS_MAX_BYTES if name == "rows.jsonl" else DOCUMENT_MAX_BYTES,
                    },
                }
            )
            for name in FILES
        ],
        "items": False,
    }
    definitions["analysis"] = tagged(
        [
            object_schema({"state": {"const": "result"}, "report": reference("evidence_result")}),
            object_schema({"state": {"const": "error"}, "error": reference("evidence_error")}),
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
                                    "files_checked": {"const": 7},
                                    "links_checked": {"const": 0},
                                    "findings": {"maxItems": 0},
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
        "$id": "urn:underwrite:contract:baseline_record.v1",
        "title": "baseline_record.v1",
        "description": (
            "Complete five-file baseline capture, descriptive analysis, "
            "and private record outcome. "
            "Stored-byte verification does not authenticate the producer or approve claims. "
            "Null published_record means no identity was returned, not proven absence. "
            "Runtime owns cross-field identity equality. Incomplete capture emits standalone "
            "evidence_error.v1 on stderr."
        ),
        **object_schema(
            {
                "schema": {"const": "baseline_record.v1"},
                "command": {"const": "records audit-baseline"},
                "source": object_schema({"state": {"const": "captured"}, "files": files}),
                "analysis": reference("analysis"),
                "storage": reference("storage"),
                "exit_code": {"enum": [0, 2, 3]},
            }
        ),
        "$defs": definitions,
        "allOf": [
            {
                "if": _state("storage", "error"),
                "then": {"properties": {"exit_code": {"const": 3}}},
                "else": {
                    "if": _state("analysis", "error"),
                    "then": {"properties": {"exit_code": {"const": 2}}},
                    "else": {"properties": {"exit_code": {"const": 0}}},
                },
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(schema_main(build_schema(), TARGET, __doc__))
