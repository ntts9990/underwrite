"""The one bounded local JSON object reader of the CLI commands (``measure``, ``accept``).

A failure carries a fixed reason, never received text, and the context-free code that
reason implies (``None`` for invalid input, whose code each command names itself).
``read_document`` and ``validate`` raise it, or a schema refusal, as the command's error."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import CanonicalizationError, content_digest

from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.instrument.ingest.transport import (
    DecodeError,
    TransportError,
    decode_json,
    read_local_file,
)

CODES = {
    "BYTE_LIMIT_EXCEEDED": "INPUT_LIMIT_EXCEEDED",
    "DEPTH_LIMIT_EXCEEDED": "INPUT_LIMIT_EXCEEDED",
    "SOURCE_NOT_REGULAR_FILE": "SOURCE_READ_FAILED",
    "SOURCE_READ_FAILED": "SOURCE_READ_FAILED",
    "UNSUPPORTED_PLATFORM": "UNSUPPORTED_PLATFORM",
}
# A command's error envelope: ``error(code, reason, location)``.
CommandError = Callable[[str, str, str], Exception]


class Validator(Protocol):
    """A compiled JSON Schema validator, as the CLI commands use one."""

    def validate(self, instance: object) -> None: ...


class LocalJsonError(ValueError):
    """``reason`` is fixed; ``code`` is None when the reason means invalid input."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = CODES.get(reason)


def read_object(path: Path, max_bytes: int, max_depth: int) -> tuple[dict[str, Any], str]:
    """Return one strictly decoded JSON object and its canonical content digest."""
    try:
        decoded = decode_json(read_local_file(path, max_bytes), max_depth)
        if not isinstance(decoded, dict):
            raise LocalJsonError("OBJECT_REQUIRED")
        document = cast("dict[str, Any]", decoded)
        return document, content_digest(document)
    except TransportError as exc:
        raise LocalJsonError(str(exc)) from None
    except DecodeError as exc:
        if exc.args == ("DEPTH_LIMIT_EXCEEDED",):
            raise LocalJsonError("DEPTH_LIMIT_EXCEEDED") from None
        raise LocalJsonError("INVALID_JSON_INPUT") from None
    except CanonicalizationError:
        raise LocalJsonError("NONCANONICAL_INPUT") from None


def read_document(
    path: Path, max_bytes: int, max_depth: int, location: str, invalid: str, error: CommandError
) -> tuple[dict[str, Any], str]:
    """``read_object``, failing as ``error``; an unsupported platform has no location."""
    try:
        return read_object(path, max_bytes, max_depth)
    except LocalJsonError as exc:
        where = "" if exc.code == "UNSUPPORTED_PLATFORM" else location
        raise error(exc.code or invalid, exc.reason, where) from None


def validate(
    validator: Validator, value: object, error: CommandError, code: str, location: str = ""
) -> None:
    """A schema refusal is ``error``; any other validator failure is a configuration fault."""
    try:
        validator.validate(value)
    except ValidationError:
        raise error(code, "SCHEMA_VALIDATION_FAILED", location) from None
    except Exception as exc:
        raise SchemaConfigurationError("SCHEMA_EVALUATION_FAILED") from exc
