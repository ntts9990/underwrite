"""Measure the registered CLI and model-visible read-only agent surface."""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from typing import TypedDict, cast

from underwrite_core.canonical import canonical_bytes

from underwrite.agent_surface.tools import catalog
from underwrite.cli import main as cli_main


class DuplicateSchemaGroup(TypedDict):
    tools: list[str]
    schema_canonical_json_bytes: int
    duplicate_bytes: int


class Metrics(TypedDict):
    schema: str
    descriptor_commands: int
    descriptor_profiles: int
    capabilities_json_stdout_bytes: int
    tool_description_utf8_bytes: dict[str, int]
    tool_canonical_json_bytes: dict[str, int]
    output_schema_canonical_json_bytes: dict[str, int]
    output_schema_total_bytes: int
    output_schema_unique_bytes: int
    output_schema_duplicate_bytes: int
    output_schema_duplicate_groups: list[DuplicateSchemaGroup]
    tools_list_canonical_json_bytes: int


def _capabilities_stdout() -> bytes:
    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = cli_main.main(["capabilities", "--json"])
    if exit_code != 0:
        raise RuntimeError("CAPABILITIES_MEASUREMENT_FAILED")
    return output.getvalue().encode("utf-8")


def _tool_catalog() -> tuple[list[dict[str, object]], dict[str, int]]:
    tools = catalog()
    description_bytes: dict[str, int] = {}
    for tool in tools:
        name = tool.get("name")
        description = tool.get("description")
        if not isinstance(name, str) or not name or name in description_bytes:
            raise ValueError("TOOL_NAME_INVALID")
        if not isinstance(description, str):
            raise ValueError("TOOL_DESCRIPTION_MISSING")
        if "Use when:" not in description or "Do not use when:" not in description:
            raise ValueError("TOOL_DESCRIPTION_GUIDANCE_MISSING")
        description_bytes[name] = len(description.encode("utf-8"))
    return tools, dict(sorted(description_bytes.items()))


def _schema_costs(
    tools: list[dict[str, object]],
) -> tuple[dict[str, int], int, int, list[DuplicateSchemaGroup]]:
    per_tool: dict[str, int] = {}
    groups: dict[bytes, list[str]] = {}
    for tool in tools:
        name = tool["name"]
        if type(name) is not str:
            raise ValueError("TOOL_NAME_INVALID")
        schema = tool.get("outputSchema")
        if not isinstance(schema, dict):
            raise ValueError("TOOL_OUTPUT_SCHEMA_MISSING")
        encoded = canonical_bytes(cast(dict[str, object], schema))
        per_tool[name] = len(encoded)
        groups.setdefault(encoded, []).append(name)
    total = sum(per_tool.values())
    unique = sum(len(encoded) for encoded in groups)
    duplicates: list[DuplicateSchemaGroup] = [
        {
            "tools": names,
            "schema_canonical_json_bytes": len(encoded),
            "duplicate_bytes": len(encoded) * (len(names) - 1),
        }
        for encoded, names in groups.items()
        if len(names) > 1
    ]
    return dict(sorted(per_tool.items())), total, unique, duplicates


def measure() -> Metrics:
    """Return versioned byte-exact measurements from the installed registrations."""
    descriptors = cli_main.COMMAND_DESCRIPTORS
    tools, description_bytes = _tool_catalog()
    schema_bytes, schema_total, schema_unique, duplicate_groups = _schema_costs(tools)
    return {
        "schema": "underwrite.agent_surface_metrics.v1",
        "descriptor_commands": len(descriptors),
        "descriptor_profiles": sum(len(descriptor.profiles) for descriptor in descriptors),
        "capabilities_json_stdout_bytes": len(_capabilities_stdout()),
        "tool_description_utf8_bytes": description_bytes,
        "tool_canonical_json_bytes": dict(
            sorted((cast(str, tool["name"]), len(canonical_bytes(tool))) for tool in tools)
        ),
        "output_schema_canonical_json_bytes": schema_bytes,
        "output_schema_total_bytes": schema_total,
        "output_schema_unique_bytes": schema_unique,
        "output_schema_duplicate_bytes": schema_total - schema_unique,
        "output_schema_duplicate_groups": duplicate_groups,
        "tools_list_canonical_json_bytes": len(canonical_bytes({"tools": tools})),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    json.dump(measure(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
