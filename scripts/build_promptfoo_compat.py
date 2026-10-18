"""Generate Promptfoo SummaryV3 auto results and record from existing owners."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from _record_schema import build_record_schema
from _schema_compose import object_schema

from underwrite.cli.record_promptfoo import AUTO_RECORD_SCHEMA, COMMAND
from underwrite.instrument.evidence.promptfoo_compat import (
    ACCOUNTING_SCHEMA,
    LIMITATIONS,
    MAX_VERSION_LENGTH,
    READER_PROFILE,
    ROW_INDEX_SCHEMA,
    ROW_LIMITATIONS,
    SCOPE,
    SOURCE_FORMAT,
    SUMMARY_VERSION,
    VERSION_PATTERN,
    VERSION_STATES,
)

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTING_V1 = ROOT / "contracts/promptfoo_accounting.v1.schema.json"
ROW_V1 = ROOT / "contracts/promptfoo_row_index.v1.schema.json"
DEFINITIONS = ("hash", "counts", "deltas", "nonnegative", "signed", "finding")
COUNT_FIELDS = ("producer_stats", "row_tallies", "deltas", "declared_relation", "findings")


def _source() -> dict[str, Any]:
    version = {
        "type": ["string", "null"],
        "maxLength": MAX_VERSION_LENGTH,
        "pattern": VERSION_PATTERN,
    }
    result = object_schema(
        {
            "format": {"const": SOURCE_FORMAT},
            "summary_version": {"const": SUMMARY_VERSION},
            "producer_version": version,
            "producer_version_state": {"enum": list(VERSION_STATES)},
            "claims_verified": {"const": False},
        }
    )
    result["oneOf"] = [
        {
            "properties": {
                "producer_version_state": {"const": state},
                "producer_version": {"type": "null" if state == "unknown" else "string"},
            }
        }
        for state in VERSION_STATES
    ]
    return result


def _scope(*, row_index: bool) -> dict[str, Any]:
    selected = {key: {"const": value} for key, value in SCOPE.items()}
    if row_index:
        selected["values"] = {"const": "withheld"}
    return object_schema(selected)


def _base(
    name: str, *, description: str, properties: dict[str, Any], owner: dict[str, Any]
) -> dict[str, Any]:
    definitions = cast(dict[str, Any], owner["$defs"])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + name,
        "title": name,
        "description": description,
        **object_schema(properties),
        "$defs": {key: deepcopy(definitions[key]) for key in DEFINITIONS},
    }


def build_accounting_schema() -> dict[str, Any]:
    owner = cast(dict[str, Any], json.loads(ACCOUNTING_V1.read_bytes()))
    fields = cast(dict[str, Any], owner["properties"])
    return _base(
        ACCOUNTING_SCHEMA,
        description=(
            "Selected Promptfoo SummaryV3 row-count agreement. Producer version is only a "
            "source declaration; unconsumed fields and populations are not analyzed."
        ),
        properties={
            "schema": {"const": ACCOUNTING_SCHEMA},
            "source": _source(),
            "raw_hash": deepcopy(fields["raw_hash"]),
            "reader_profile": {"const": READER_PROFILE},
            "scope": _scope(row_index=False),
            **{key: deepcopy(fields[key]) for key in COUNT_FIELDS},
            "limitations": {"const": list(LIMITATIONS)},
        },
        owner=owner,
    )


def build_row_index_schema() -> dict[str, Any]:
    accounting = build_accounting_schema()
    old_row = cast(dict[str, Any], json.loads(ROW_V1.read_bytes()))
    fields = cast(dict[str, Any], accounting["properties"])
    old_fields = cast(dict[str, Any], old_row["properties"])
    return _base(
        ROW_INDEX_SCHEMA,
        description="Selected, content-minimized Promptfoo SummaryV3 source-flag rows.",
        properties={
            "schema": {"const": ROW_INDEX_SCHEMA},
            "source": deepcopy(fields["source"]),
            "raw_hash": deepcopy(fields["raw_hash"]),
            "reader_profile": deepcopy(fields["reader_profile"]),
            "scope": _scope(row_index=True),
            **{key: deepcopy(fields[key]) for key in COUNT_FIELDS},
            "rows": deepcopy(old_fields["rows"]),
            "verification": deepcopy(old_fields["verification"]),
            "limitations": {"const": list(ROW_LIMITATIONS)},
        },
        owner=accounting,
    )


def build_record_schema_v2() -> dict[str, Any]:
    owner, version = ACCOUNTING_SCHEMA.rsplit(".", 1)
    return build_record_schema(
        ROOT,
        accounting_owner=owner,
        accounting_version=version,
        schema_name=AUTO_RECORD_SCHEMA,
        command=COMMAND,
        description=(
            "Selected Promptfoo SummaryV3 accounting and private capture outcomes. "
            "Stored-byte verification is not producer authentication or approval. "
            "Null published_record means no returned identity, not proven absence."
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, builder in (
        (ACCOUNTING_SCHEMA, build_accounting_schema),
        (ROW_INDEX_SCHEMA, build_row_index_schema),
        (AUTO_RECORD_SCHEMA, build_record_schema_v2),
    ):
        target = ROOT / f"contracts/{name}.schema.json"
        raw = (json.dumps(builder(), ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not target.exists() or target.read_bytes() != raw:
                print(f"{target.name} is stale")
                return 1
        else:
            target.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
