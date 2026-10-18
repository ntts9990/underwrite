"""Generate neutral DeepEval pack, result, record, and inspection contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from _schema_compose import embed_contracts, nullable, object_schema, reference
from build_common_result_inspection_actions import build_schema as build_actions
from build_deepeval_bound_record import build_schema as build_bound_record
from build_deepeval_metric_bindings import build_schema as build_pack

from underwrite.cli.record_deepeval_bound import AUTO_SCHEMA as AUTO_RECORD_SCHEMA
from underwrite.instrument.evidence.deepeval_binding import (
    AUTO_PACK_SCHEMA,
    AUTO_RESULT_SCHEMA,
    DECLARATION_AUTHORITY,
)
from underwrite.instrument.evidence.deepeval_compat import ACCOUNTING_READERS

ROOT = Path(__file__).resolve().parents[1]
INSPECTION_SCHEMA = "common_result_inspection.v2"
ACTIONS_SCHEMA = "common_result_inspection_actions.v2"


def _authored(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    )


def build_pack_v2() -> dict[str, Any]:
    schema = build_pack()
    schema["$id"] = "urn:underwrite:contract:" + AUTO_PACK_SCHEMA
    schema["title"] = AUTO_PACK_SCHEMA
    schema["description"] = (
        "Version-neutral owner-asserted metric-name meanings for supplied ordinary "
        "DeepEval occurrences; no evaluator or producer-release verification."
    )
    schema["properties"]["schema"] = {"const": AUTO_PACK_SCHEMA}
    source = schema["properties"]["source"]
    source["properties"].pop("format_version")
    source["required"].remove("format_version")
    return schema


def build_result_v2() -> dict[str, Any]:
    schema = _authored("evidence_result.v1")
    schema["$id"] = "urn:underwrite:contract:" + AUTO_RESULT_SCHEMA
    schema["title"] = AUTO_RESULT_SCHEMA
    schema["description"] = (
        "Artifact-scoped descriptive evidence with separate producer-version claim, "
        "applied reader and owner-declaration byte identity; no decision authority."
    )
    schema["properties"]["schema"] = {"const": AUTO_RESULT_SCHEMA}
    source = schema["properties"]["source"]
    source["description"] = (
        "Null only when no source identity can be established. Producer version is a "
        "declaration or unknown, not authenticated by this contract."
    )
    source["required"] = [
        "format",
        "producer_version",
        "producer_version_state",
        "reader_profile",
        "identity",
    ]
    source["properties"].pop("format_version")
    source["properties"].update(
        {
            "producer_version": {
                **nullable(reference("version")),
                "description": "A source-declared package version or null; not authenticated.",
            },
            "producer_version_state": {"enum": ["unknown", "declared"]},
            "reader_profile": {
                **nullable(reference("token")),
                "description": "Applied local reader rule, not producer version; null if none.",
            },
        }
    )
    source["allOf"] = [
        {
            "if": {
                "properties": {"producer_version_state": {"const": "declared"}},
                "required": ["producer_version_state"],
            },
            "then": {"properties": {"producer_version": reference("version")}},
            "else": {"properties": {"producer_version": {"type": "null"}}},
        }
    ]
    declaration = nullable(
        object_schema(
            {
                "format": reference("token"),
                "raw_hash": reference("hash"),
                "authority": {"const": DECLARATION_AUTHORITY},
            }
        )
    )
    declaration["description"] = (
        "One separately captured owner declaration, if supplied. Its hash identifies "
        "bytes but does not authenticate the owner or declared meaning."
    )
    schema["properties"]["declaration_source"] = declaration
    schema["required"].append("declaration_source")
    return schema


def build_inspection_v2() -> dict[str, Any]:
    schema = _authored("common_result_inspection.v1")
    schema["$id"] = "urn:underwrite:contract:" + INSPECTION_SCHEMA
    schema["title"] = INSPECTION_SCHEMA
    schema["description"] = (
        "Content-minimized structural screening of an evidence_result.v2 declaration; "
        "no source, pack or decision authority."
    )
    schema["properties"]["schema"] = {"const": INSPECTION_SCHEMA}
    schema["properties"]["selected_result"]["properties"]["format"] = {"const": AUTO_RESULT_SCHEMA}
    return schema


def build_record_v2() -> dict[str, Any]:
    schema = build_bound_record()
    schema["$id"] = "urn:underwrite:contract:" + AUTO_RECORD_SCHEMA
    schema["title"] = AUTO_RECORD_SCHEMA
    schema["description"] = (
        "Two captured DeepEval inputs with version-neutral owner-declared meanings; "
        "stored-byte integrity grants no producer or approval authority."
    )
    schema["properties"]["schema"] = {"const": AUTO_RECORD_SCHEMA}
    schema["properties"].pop("source_format_version")
    schema["required"].remove("source_format_version")
    definitions, _ = embed_contracts(ROOT, ("evidence_result",), versions={"evidence_result": "v2"})
    for name in tuple(schema["$defs"]):
        if name == "evidence_result" or name.startswith("evidence_result__"):
            del schema["$defs"][name]
    schema["$defs"].update(definitions)
    schema["allOf"].append(
        {
            "if": {"properties": {"analysis": {"properties": {"state": {"const": "result"}}}}},
            "then": {
                "properties": {
                    "analysis": {
                        "properties": {
                            "report": {
                                "properties": {
                                    "source": {
                                        "properties": {
                                            "reader_profile": {"enum": list(ACCOUNTING_READERS)}
                                        }
                                    },
                                    "declaration_source": {
                                        "type": "object",
                                        "properties": {"format": {"const": AUTO_PACK_SCHEMA}},
                                    },
                                }
                            }
                        }
                    }
                }
            },
        }
    )
    return schema


def _write(name: str, schema: dict[str, Any], *, check: bool) -> bool:
    target = ROOT / "contracts" / f"{name}.schema.json"
    expected = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode()
    if check:
        current = target.read_bytes() if target.exists() else b""
        if current != expected:
            print(f"{target.name} is stale")
            return False
    else:
        target.write_bytes(expected)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    initial = (
        (AUTO_PACK_SCHEMA, build_pack_v2()),
        (AUTO_RESULT_SCHEMA, build_result_v2()),
        (INSPECTION_SCHEMA, build_inspection_v2()),
    )
    if not all(_write(name, schema, check=args.check) for name, schema in initial):
        return 1
    dependent = (
        (ACTIONS_SCHEMA, build_actions(version="v2")),
        (AUTO_RECORD_SCHEMA, build_record_v2()),
    )
    return int(not all(_write(name, schema, check=args.check) for name, schema in dependent))


if __name__ == "__main__":
    raise SystemExit(main())
