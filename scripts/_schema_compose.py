"""Build-time composition of existing standalone contract definitions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def reference(name: str) -> dict[str, str]:
    return {"$ref": "#/$defs/" + name}


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"oneOf": [{"type": "null"}, schema]}


def tagged(variants: list[dict[str, Any]]) -> dict[str, Any]:
    """Dispatch closed branches before traversing their bounded nested payloads."""
    states = [variant["properties"]["state"]["const"] for variant in variants]
    return {
        "type": "object",
        "required": ["state"],
        "properties": {"state": {"enum": states}},
        "allOf": [
            {"if": {"properties": {"state": {"const": state}}}, "then": variant}
            for state, variant in zip(states, variants, strict=True)
        ],
    }


def rewrite(value: Any, prefix: str) -> Any:
    if isinstance(value, list):
        return [rewrite(item, prefix) for item in cast(list[Any], value)]
    if not isinstance(value, dict):
        return value
    if "$id" in value or "$anchor" in value or "$dynamicRef" in value:
        raise ValueError("NESTED_SCHEMA_SCOPE_UNSUPPORTED")
    result: dict[str, Any] = {}
    for key, item in cast(dict[str, Any], value).items():
        if key == "$ref":
            if not isinstance(item, str) or not item.startswith("#/$defs/"):
                raise ValueError("NONLOCAL_DEFINITION_REFERENCE")
            result[key] = "#/$defs/" + prefix + "__" + item.removeprefix("#/$defs/")
        else:
            result[key] = rewrite(item, prefix)
    return result


def embed_contracts(
    root: Path,
    names: tuple[str, ...],
    *,
    versions: dict[str, str] | None = None,
    namespaces: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    definitions: dict[str, Any] = {}
    owners: dict[str, Any] = {}
    for name in names:
        version = (versions or {}).get(name, "v1")
        namespace = (namespaces or {}).get(name, name)
        source = json.loads((root / f"contracts/{name}.{version}.schema.json").read_bytes())
        owners[name] = source
        body = {k: v for k, v in source.items() if k not in {"$id", "$schema", "$defs"}}
        definitions[namespace] = rewrite(body, namespace)
        for key, value in source.get("$defs", {}).items():
            definitions[namespace + "__" + key] = rewrite(value, namespace)
    return definitions, owners
