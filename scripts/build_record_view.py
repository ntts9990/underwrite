"""Generate the standalone record_view contract from result_record's authored schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/result_record.v1.schema.json"
TARGET = ROOT / "contracts/record_view.v1.schema.json"


def _load() -> dict[str, object]:
    value = json.loads(SOURCE.read_bytes())
    if type(value) is not dict:
        raise ValueError("INVALID_RESULT_RECORD_SCHEMA")
    return cast(dict[str, object], value)


def _reject_nested_ids(value: object, *, root: bool = True) -> None:
    if isinstance(value, dict):
        if not root and "$id" in value:
            raise ValueError("NESTED_SCHEMA_ID")
        for item in cast(dict[object, object], value).values():
            _reject_nested_ids(item, root=False)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _reject_nested_ids(item, root=False)


def build_schema(source: dict[str, object]) -> dict[str, object]:
    """Embed the complete result record with only document-local references."""
    _reject_nested_ids(source)
    definitions = source.get("$defs")
    if type(definitions) is not dict:
        raise ValueError("MISSING_RESULT_RECORD_DEFINITIONS")
    record = {
        key: value
        for key, value in source.items()
        if key not in {"$schema", "$id", "title", "$defs"}
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:record_view.v1",
        "title": "record_view.v1",
        "description": "A complete published record plus its separately computed manifest digest.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "manifest_digest", "record"],
        "properties": {
            "schema": {"const": "record_view.v1"},
            "manifest_digest": {"$ref": "#/$defs/digest"},
            "record": {"$ref": "#/$defs/result_record"},
        },
        "$defs": {
            **cast(dict[str, object], definitions),
            "result_record": record,
        },
    }


def _encoded() -> bytes:
    return (json.dumps(build_schema(_load()), ensure_ascii=False, indent=2) + "\n").encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail if the generated contract is stale."
    )
    args = parser.parse_args(argv)
    expected = _encoded()
    if args.check:
        try:
            current = TARGET.read_bytes()
        except OSError:
            current = b""
        if current != expected:
            print("record_view.v1.schema.json is stale", file=sys.stderr)
            return 1
        return 0
    TARGET.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
