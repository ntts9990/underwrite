"""Load the explicitly deployed observation contract once and reuse its validator."""

from __future__ import annotations

import json
from importlib.resources import as_file, files
from pathlib import Path
from typing import TypeGuard

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


class SchemaConfigurationError(ValueError):
    """The trusted deployed observation contract is missing or invalid."""


class ObservationValidationError(ValueError):
    """Projected facts do not satisfy the configured observation contract."""


def packaged_validator(name: str) -> Draft202012Validator:
    """Load a caller-selected trusted contract; names never come from an artifact."""
    resource = files("underwrite").joinpath("_contracts", f"{name}.schema.json")
    try:
        with as_file(resource) as path:
            schema: object = json.loads(path.read_text(encoding="utf-8"))
        if not _is_dict(schema) or schema.get("$id") != f"urn:underwrite:contract:{name}":
            raise SchemaConfigurationError("WRONG_INSPECTION_CONTRACT")
        _check_local_references(schema)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, SchemaError) as exc:
        raise SchemaConfigurationError("INVALID_INSPECTION_SCHEMA") from exc


def _is_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _check_local_references(schema: dict[str, object]) -> None:
    pending: list[object] = [schema]
    # Iterate the growing worklist: no pop/assignment a defect could turn into a spin.
    for node in pending:
        if _is_dict(node):
            if node is not schema and "$id" in node:
                raise SchemaConfigurationError("NESTED_SCHEMA_RESOURCE")
            for name, value in node.items():
                if name in {"$ref", "$dynamicRef"}:
                    if not isinstance(value, str) or not value.startswith("#"):
                        raise SchemaConfigurationError("NON_LOCAL_SCHEMA_REFERENCE")
                pending.append(value)
        elif _is_list(node):
            pending.extend(node)


class ObservationSchema:
    """Load/check the schema at configuration; resolve internal pointers on use.

    Invalid internal pointers are typed configuration failures during validation.
    No request validation callback or remote schema reference is accepted.
    """

    def __init__(self, path: Path) -> None:
        try:
            schema: object = json.loads(path.read_text(encoding="utf-8"))
            if (
                not _is_dict(schema)
                or schema.get("$id") != "urn:underwrite:contract:observation.v1"
            ):
                raise SchemaConfigurationError("WRONG_OBSERVATION_CONTRACT")
            _check_local_references(schema)
            Draft202012Validator.check_schema(schema)
            self._validator = Draft202012Validator(schema)
        except (OSError, UnicodeError, json.JSONDecodeError, SchemaError) as exc:
            raise SchemaConfigurationError("INVALID_OBSERVATION_SCHEMA") from exc

    def validate(self, observation: dict[str, object]) -> None:
        """The one schema validation point after successful projection."""
        try:
            # jsonschema has no py.typed marker; isolate its untyped call here.
            self._validator.validate(observation)  # pyright: ignore[reportUnknownMemberType]
        except ValidationError as exc:
            raise ObservationValidationError("INVALID_OBSERVATION") from exc
        except Exception as exc:
            # jsonschema wraps resolution failures in a private exception type.
            # Preserve its cause without coupling to that private/transitive API.
            raise SchemaConfigurationError("OBSERVATION_SCHEMA_EVALUATION_FAILED") from exc
