"""Generate two closed Langfuse auto views and the existing two-source record envelope."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from _schema_compose import object_schema
from build_langfuse_record import build_auto_schema

from underwrite.cli.record_langfuse import AUTO_RECORD_SCHEMA
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    BLOB_READER,
    CLI_READER,
    EXTRA_SCOPE,
    ROLES,
    ROW_INDEX_SCHEMA,
    SOURCE_FORMATS,
    extended_limitations,
)

ROOT = Path(__file__).resolve().parents[1]
OWNERS = {
    ACCOUNTING_SCHEMA: ("langfuse_accounting.v1", "langfuse_cli_accounting.v1"),
    ROW_INDEX_SCHEMA: ("langfuse_row_index.v1", "langfuse_cli_row_index.v1"),
}


def _sources(owner: dict[str, Any], reader: str) -> dict[str, Any]:
    old = cast(dict[str, Any], owner["properties"]["sources"]["properties"])
    return object_schema(
        {
            role: object_schema(
                {
                    "format": {"const": SOURCE_FORMATS[reader][role]},
                    "raw_hash": deepcopy(old[role]["properties"]["raw_hash"]),
                    "producer_version": {"type": "null"},
                    "producer_version_state": {"const": "unknown"},
                    "claims_verified": {"const": False},
                }
            )
            for role in ROLES
        }
    )


def _scope(owner: dict[str, Any]) -> dict[str, Any]:
    old = deepcopy(owner["properties"]["scope"])
    old["properties"].update({key: {"const": value} for key, value in EXTRA_SCOPE.items()})
    old["required"] = list(dict.fromkeys((*old["required"], *EXTRA_SCOPE)))
    return cast(dict[str, Any], old)


def _branch(name: str, owner_name: str, reader: str) -> dict[str, Any]:
    owner = cast(
        dict[str, Any], json.loads((ROOT / f"contracts/{owner_name}.schema.json").read_bytes())
    )
    if owner.get("$defs"):
        raise ValueError("LANGFUSE_COMPAT_OWNER_DEFINITIONS_UNEXPECTED")
    branch = deepcopy(
        {
            key: value
            for key, value in owner.items()
            if key not in {"$id", "$schema", "title", "description", "$defs"}
        }
    )
    properties = cast(dict[str, Any], branch["properties"])
    properties["schema"] = {"const": name}
    properties["reader_profile"] = {"const": reader}
    properties["sources"] = _sources(owner, reader)
    properties["scope"] = _scope(owner)
    old_limits = owner["properties"]["limitations"]["const"]
    properties["limitations"] = {"const": extended_limitations(old_limits)}
    branch["required"].append("reader_profile")
    return branch


def _view(name: str) -> dict[str, Any]:
    blob_owner, cli_owner = OWNERS[name]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + name,
        "title": name,
        "description": (
            "Selected Langfuse array/blob or CLI-list rows; producer version and source windows "
            "remain unknown. The reader family is an Underwrite policy, not origin proof."
        ),
        "if": {
            "properties": {"reader_profile": {"const": BLOB_READER}},
            "required": ["reader_profile"],
        },
        "then": _branch(name, blob_owner, BLOB_READER),
        "else": _branch(name, cli_owner, CLI_READER),
    }


def build_accounting_schema() -> dict[str, Any]:
    return _view(ACCOUNTING_SCHEMA)


def build_row_index_schema() -> dict[str, Any]:
    return _view(ROW_INDEX_SCHEMA)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, build in (
        (ACCOUNTING_SCHEMA, build_accounting_schema),
        (ROW_INDEX_SCHEMA, build_row_index_schema),
        (AUTO_RECORD_SCHEMA, build_auto_schema),
    ):
        target = ROOT / f"contracts/{name}.schema.json"
        raw = (json.dumps(build(), ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not target.exists() or target.read_bytes() != raw:
                print(f"{target.name} is stale")
                return 1
        else:
            target.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
