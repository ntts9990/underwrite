"""Generate native measurement capture from the measurement and record owners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _record_schema import schema_main, storage_definition
from _schema_compose import embed_contracts, object_schema, reference, tagged

from underwrite.cli.measure import OBSERVATION_MAX_BYTES, POLICY_MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/measurement_record.v1.schema.json"


def _at(path: str, value: dict[str, Any]) -> dict[str, Any]:
    for part in reversed(path.split("/")):
        value = {"properties": {part: value}}
    return value


def _state(path: str, state: str) -> dict[str, Any]:
    return _at(path + "/state", {"const": state})


def build_schema() -> dict[str, Any]:
    definitions, _ = embed_contracts(
        ROOT,
        ("read", "measurement_error", "record_error", "record_verification"),
        versions={"read": "v2"},
    )
    for role, maximum in (("observation", OBSERVATION_MAX_BYTES), ("policy", POLICY_MAX_BYTES)):
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
                    {"state": {"const": "unavailable"}, "error": reference("measurement_error")}
                ),
            ]
        )
    definitions["analysis"] = tagged(
        [
            object_schema({"state": {"const": "result"}, "report": reference("read")}),
            object_schema(
                {
                    "state": {"const": "error"},
                    "phase": {"enum": ["acquisition", "analysis"]},
                    "error": reference("measurement_error"),
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
                    _state("sources/observation", "captured"),
                    _state("sources/policy", "captured"),
                ]
            },
        },
        {
            "if": _state("sources/observation", "unavailable"),
            "then": {
                "allOf": [
                    _state("analysis", "error"),
                    _at("analysis/phase", {"const": "acquisition"}),
                    _at("analysis/error/location", {"enum": ["", "/observation"]}),
                ]
            },
            "else": {
                "if": _state("sources/policy", "captured"),
                "then": _at("analysis/phase", {"const": "analysis"}),
                "else": {
                    "allOf": [
                        _state("analysis", "error"),
                        {
                            "if": _at("analysis/phase", {"const": "acquisition"}),
                            "then": _at("analysis/error/location", {"enum": ["", "/policy"]}),
                            "else": _at("analysis/error/location", {"enum": ["", "/observation"]}),
                        },
                    ]
                },
            },
        },
    ]
    for observation in ("captured", "unavailable"):
        for policy in ("captured", "unavailable"):
            count = 2 + (observation == "captured") + (policy == "captured")
            rules.append(
                {
                    "if": {
                        "allOf": [
                            _state("storage", "saved"),
                            _state("sources/observation", observation),
                            _state("sources/policy", policy),
                        ]
                    },
                    "then": _at("storage/verification/files_checked", {"const": count}),
                }
            )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:measurement_record.v1",
        "title": "measurement_record.v1",
        "description": (
            "Native measurement and private capture outcomes. Stored-byte integrity does not "
            "authenticate producers or authorize approval. Null published_record means no "
            "identity was returned, not proven absence. Runtime owns cross-field identity "
            "equality and selected error precedence."
        ),
        **object_schema(
            {
                "schema": {"const": "measurement_record.v1"},
                "command": {"const": "records measure"},
                "sources": object_schema(
                    {"observation": reference("observation"), "policy": reference("policy")}
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
