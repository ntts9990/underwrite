"""Compose the opt-in two-source DeepEval bound capture from existing contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

if __package__:
    from ._record_schema import schema_main, storage_definition
    from ._schema_compose import embed_contracts, object_schema, reference, tagged
else:
    from _record_schema import schema_main, storage_definition
    from _schema_compose import embed_contracts, object_schema, reference, tagged

from underwrite.cli.record_deepeval_bound import AUTO_SCHEMA
from underwrite.instrument.evidence.deepeval_binding import AUTO_PACK_SCHEMA, AUTO_RESULT_SCHEMA
from underwrite.instrument.evidence.deepeval_compat import ACCOUNTING_READERS
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES
from underwrite.instrument.ingest.deepeval_binding import PACK_MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / f"contracts/{AUTO_SCHEMA}.schema.json"


def _at(path: str, value: dict[str, Any]) -> dict[str, Any]:
    for part in reversed(path.split("/")):
        value = {"properties": {part: value}}
    return value


def _state(path: str, state: str) -> dict[str, Any]:
    return _at(path + "/state", {"const": state})


def build_schema() -> dict[str, Any]:
    definitions, _ = embed_contracts(
        ROOT, ("evidence_error", "record_error", "record_verification")
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
    definitions["storage"] = storage_definition({"enum": [2, 3, 4]})
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
    owner, version = AUTO_RESULT_SCHEMA.rsplit(".", 1)
    result_definitions, _ = embed_contracts(ROOT, (owner,), versions={owner: version})
    definitions.update(result_definitions)
    rules.append(
        {
            "if": _state("analysis", "result"),
            "then": _at(
                "analysis/report",
                {
                    "properties": {
                        "source": _at("reader_profile", {"enum": list(ACCOUNTING_READERS)}),
                        "declaration_source": {
                            "type": "object",
                            "properties": {"format": {"const": AUTO_PACK_SCHEMA}},
                        },
                    },
                },
            ),
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + AUTO_SCHEMA,
        "title": AUTO_SCHEMA,
        "description": (
            "Two captured DeepEval inputs with version-neutral owner-declared meanings; "
            "stored-byte integrity grants no producer or approval authority."
        ),
        **object_schema(
            {
                "schema": {"const": AUTO_SCHEMA},
                "command": {"const": "records audit-deepeval"},
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
