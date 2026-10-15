"""Generate the opt-in inspection contract from the two authored owners."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from _schema_compose import nullable, object_schema

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/common_result_inspection_actions.v1.schema.json"
NAME = "common_result_inspection_actions.v1"


def _owner(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((ROOT / f"contracts/{name}.schema.json").read_bytes()))


def _histogram(names: list[str]) -> dict[str, Any]:
    return nullable(object_schema({name: {"type": "integer", "minimum": 0} for name in names}))


def build_schema() -> dict[str, Any]:
    base = _owner("common_result_inspection.v1")
    source = _owner("evidence_result.v1")
    definitions = source["$defs"]
    actions = definitions["item"]["properties"]["next_action"]["enum"]
    bindings = [
        variant["properties"]["semantic_binding"]["const"]
        for variant in definitions["metric_binding"]["oneOf"]
    ]
    fields = ("item_next_action_counts", "declared_semantic_binding_counts")
    base["$id"] = "urn:underwrite:contract:" + NAME
    base["title"] = NAME
    base["description"] = (
        "Content-minimized screening with item action and source-declared semantic-binding "
        "histograms; no source or decision authority."
    )
    base["properties"]["schema"] = {"const": NAME}
    base["properties"][fields[0]] = _histogram(actions)
    base["properties"][fields[1]] = _histogram(bindings)
    base["required"].extend(fields)
    branches = base["allOf"][0]
    for branch, kind in (("then", "object"), ("else", "null")):
        branches[branch]["properties"].update({field: {"type": kind} for field in fields})
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail if the generated contract is stale."
    )
    args = parser.parse_args(argv)
    expected = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        try:
            current = TARGET.read_bytes()
        except OSError:
            current = b""
        if current != expected:
            print(f"{TARGET.name} is stale", file=sys.stderr)
            return 1
        return 0
    TARGET.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
