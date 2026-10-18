"""Card: current-contract-consolidation | Paper: CONTRACT_MIGRATION.md | Stage: S5

Build current inspection views from the exact authored common-result input owners.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from underwrite_core.canonical import CanonicalizationError, canonical_bytes

if __package__:
    from ._schema_compose import nullable, object_schema
else:
    from _schema_compose import nullable, object_schema

from underwrite.cli.inspect_result import (
    ACTIONS_SCHEMA,
    INPUT_SCHEMAS,
    LIMITATIONS,
    SCHEMA,
    closed_vocabulary_names,
    inspection_vocabulary,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError

ROOT = Path(__file__).resolve().parents[1]


def _no_references(value: object) -> None:
    """Consumed fragments must stand alone; this builder does not resolve references."""
    if isinstance(value, dict):
        fields = cast(dict[str, object], value)
        if {"$ref", "$dynamicRef", "$recursiveRef"} & fields.keys():
            raise SchemaConfigurationError("REFERENCED_INSPECTION_INPUT_FRAGMENT")
        for child in fields.values():
            _no_references(child)
    elif isinstance(value, list):
        for child in cast(list[object], value):
            _no_references(child)


def _consumed(owner: dict[str, Any]) -> dict[str, Any]:
    try:
        actions, bindings = inspection_vocabulary(owner)
        availability = owner["$defs"]["item"]["properties"]["availability"]
        names = closed_vocabulary_names(availability["enum"])
        fields = {
            "processing": owner["properties"]["processing"],
            "population": owner["$defs"]["population"],
            "availability": availability,
            "action_definition": owner["$defs"]["item"]["properties"]["next_action"],
            "availability_names": list(names),
            "action_names": list(actions),
            "binding_names": list(bindings),
        }
    except (KeyError, TypeError, AttributeError):
        raise SchemaConfigurationError("INVALID_INSPECTION_INPUT_CONTRACT") from None
    _no_references(fields)
    return fields


def _shared_fields() -> dict[str, Any]:
    selected: dict[str, Any] | None = None
    for name in INPUT_SCHEMAS:
        owner = cast(
            dict[str, Any], json.loads((ROOT / f"contracts/{name}.schema.json").read_bytes())
        )
        fields = _consumed(owner)
        try:
            if selected is not None and canonical_bytes(fields) != canonical_bytes(selected):
                raise SchemaConfigurationError("INCOMPATIBLE_INSPECTION_INPUT_CONTRACTS")
        except CanonicalizationError:
            raise SchemaConfigurationError("INVALID_INSPECTION_INPUT_CONTRACT") from None
        selected = fields
    assert selected is not None
    return selected


def _histogram(names: list[str]) -> dict[str, Any]:
    return nullable(object_schema({name: {"type": "integer", "minimum": 0} for name in names}))


def _summary(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + SCHEMA,
        "title": SCHEMA,
        "description": (
            "Content-minimized structural screening of the exact selected common-result "
            "input; source claims and verification references remain unchecked."
        ),
        **object_schema(
            {
                "schema": {"const": SCHEMA},
                "selected_result": object_schema(
                    {
                        "format": {"enum": list(INPUT_SCHEMAS)},
                        "raw_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    }
                ),
                "declared_processing": deepcopy(fields["processing"]),
                "declared_population": nullable(deepcopy(fields["population"])),
                "availability_counts": _histogram([*fields["availability_names"], "total"]),
                "checks": object_schema(
                    {
                        "contract_shape": {"const": "valid"},
                        "item_invariants": {"enum": ["valid", "not_applicable"]},
                        "source_bytes": {"const": "not_checked"},
                        "producer_authentication": {"const": "not_checked"},
                        "verification_refs": {"const": "not_reperformed"},
                    }
                ),
                "verification_entry_count": {"type": "integer", "minimum": 0},
                "limitations": {"const": list(LIMITATIONS)},
            }
        ),
        "allOf": [
            {
                "if": {
                    "properties": {
                        "declared_processing": {"properties": {"state": {"const": "analyzed"}}}
                    }
                },
                "then": {
                    "properties": {
                        "declared_population": {"type": "object"},
                        "availability_counts": {"type": "object"},
                        "checks": {"properties": {"item_invariants": {"const": "valid"}}},
                    }
                },
                "else": {
                    "properties": {
                        "declared_population": {"type": "null"},
                        "availability_counts": {"type": "null"},
                        "checks": {"properties": {"item_invariants": {"const": "not_applicable"}}},
                    }
                },
            }
        ],
    }


def build_schemas() -> dict[str, dict[str, Any]]:
    fields = _shared_fields()
    summary = _summary(fields)
    actions = deepcopy(summary)
    actions["$id"] = "urn:underwrite:contract:" + ACTIONS_SCHEMA
    actions["title"] = ACTIONS_SCHEMA
    actions["description"] = (
        "Content-minimized screening with source-declared action and semantic-binding "
        "histograms over the exact selected common-result input; no decision authority."
    )
    actions["properties"]["schema"] = {"const": ACTIONS_SCHEMA}
    for field, names in (
        ("item_next_action_counts", fields["action_names"]),
        ("declared_semantic_binding_counts", fields["binding_names"]),
    ):
        actions["properties"][field] = _histogram(names)
        actions["required"].append(field)
        for branch, kind in (("then", "object"), ("else", "null")):
            actions["allOf"][0][branch]["properties"][field] = {"type": kind}
    result = {SCHEMA: summary, ACTIONS_SCHEMA: actions}
    for schema in result.values():
        Draft202012Validator.check_schema(schema)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, schema in build_schemas().items():
        target = ROOT / f"contracts/{name}.schema.json"
        encoded = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not target.exists() or target.read_bytes() != encoded:
                print(f"{target.name} is stale")
                return 1
        else:
            target.write_bytes(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
