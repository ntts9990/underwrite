"""Compose the opt-in two-source DeepEval bound capture from existing contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _record_schema import schema_main
from _schema_compose import embed_contracts, nullable, object_schema, reference, tagged

from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES
from underwrite.instrument.ingest.deepeval_binding import PACK_MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/deepeval_bound_record.v1.schema.json"


def _at(path: str, value: dict[str, Any]) -> dict[str, Any]:
    for part in reversed(path.split("/")):
        value = {"properties": {part: value}}
    return value


def _state(path: str, state: str) -> dict[str, Any]:
    return _at(path + "/state", {"const": state})


def build_schema() -> dict[str, Any]:
    definitions, _ = embed_contracts(
        ROOT, ("evidence_result", "evidence_error", "record_error", "record_verification")
    )
    for role, maximum in (("bindings", PACK_MAX_BYTES), ("deepeval", DEFAULT_MAX_BYTES)):
        definitions[role] = tagged(
            [
                object_schema(
                    {
                        "state": {"const": "captured"},
                        "raw_hash": reference("record_verification__digest"),
                        "bytes": {"type": "integer", "minimum": 0, "maximum": maximum},
                    }
                ),
                object_schema(
                    {"state": {"const": "unavailable"}, "error": reference("evidence_error")}
                ),
            ]
        )
    definitions["analysis"] = tagged(
        [
            object_schema({"state": {"const": "result"}, "report": reference("evidence_result")}),
            object_schema(
                {
                    "state": {"const": "error"},
                    "phase": {"enum": ["acquisition", "analysis"]},
                    "error": reference("evidence_error"),
                }
            ),
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
    rules: list[dict[str, Any]] = [
        {
            "if": _state("storage", "error"),
            "then": _at("exit_code", {"const": 3}),
            "else": {
                "if": _state("analysis", "error"),
                "then": _at("exit_code", {"const": 2}),
                "else": _at("exit_code", {"const": 0}),
            },
        },
        {
            "if": _state("analysis", "result"),
            "then": {
                "allOf": [
                    _state("sources/bindings", "captured"),
                    _state("sources/deepeval", "captured"),
                ]
            },
        },
        {
            "if": _state("sources/bindings", "unavailable"),
            "then": {
                "allOf": [
                    _state("analysis", "error"),
                    _at("analysis/phase", {"const": "acquisition"}),
                    _at("analysis/error/location", {"enum": ["", "/bindings"]}),
                ]
            },
            "else": {
                "if": _state("sources/deepeval", "unavailable"),
                "then": {
                    "allOf": [
                        _state("analysis", "error"),
                        _at("analysis/phase", {"const": "acquisition"}),
                        _at("analysis/error/location", {"enum": ["", "/file"]}),
                    ]
                },
                "else": {
                    "if": _state("analysis", "error"),
                    "then": _at("analysis/phase", {"const": "analysis"}),
                },
            },
        },
    ]
    for bindings in ("captured", "unavailable"):
        for deepeval in ("captured", "unavailable"):
            count = 2 + (bindings == "captured") + (deepeval == "captured")
            rules.append(
                {
                    "if": {
                        "allOf": [
                            _state("storage", "saved"),
                            _state("sources/bindings", bindings),
                            _state("sources/deepeval", deepeval),
                        ]
                    },
                    "then": _at("storage/verification/files_checked", {"const": count}),
                }
            )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:deepeval_bound_record.v1",
        "title": "deepeval_bound_record.v1",
        "description": (
            "Opt-in DeepEval source and owner binding pack capture. Raw pack hash identifies "
            "bytes; metric semantic digests identify owner declarations, not authenticated truth. "
            "Stored-byte integrity does not establish source provenance or approval."
        ),
        **object_schema(
            {
                "schema": {"const": "deepeval_bound_record.v1"},
                "command": {"const": "records audit-deepeval"},
                "source_format_version": {"const": "4.2.8"},
                "sources": object_schema(
                    {"bindings": reference("bindings"), "deepeval": reference("deepeval")}
                ),
                "analysis": reference("analysis"),
                "storage": reference("storage"),
                "exit_code": {"enum": [0, 2, 3]},
            }
        ),
        "$defs": definitions,
        "allOf": rules,
    }


if __name__ == "__main__":
    raise SystemExit(schema_main(build_schema(), TARGET, __doc__))
