"""Bounded I/O and diagnostics for the independent evidence pilot commands."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.local_json import Validator, read_document, validate
from underwrite.error_contract.registry import EVIDENCE_ACTIONS, evidence_projection
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

REPORT_MAX_BYTES = 1024 * 1024
DISPLAY_LIMIT = 1024
type OutputSchemas = str | tuple[str, ...]


def display(value: object) -> str:
    """One escaped JSON value for human output, bounded and marked if truncated."""
    text = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return text if len(text) <= DISPLAY_LIMIT else text[:DISPLAY_LIMIT] + " [truncated]"


@dataclass(frozen=True)
class ReportOutput:
    """Output contract and presentation for a composed CLI report."""

    schema: OutputSchemas | Callable[[argparse.Namespace], OutputSchemas]
    max_bytes: int = REPORT_MAX_BYTES
    exit_status: Callable[[dict[str, object]], int] | None = None
    human: Callable[[dict[str, object]], str] | None = None


def output_line_size(encoded: str | bytes) -> int:
    """Account for the exact UTF-8 payload plus the emitter's final newline."""
    return len(encoded.encode("utf-8") if isinstance(encoded, str) else encoded) + 1


ERROR_ACTIONS = EVIDENCE_ACTIONS
_REASON_TOKEN = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_LOCATION_SEGMENT = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_.~-]*|0|[1-9][0-9]*)\Z")
_INVALID_POINTER_ESCAPE = re.compile(r"~(?![01])")
_LEGACY_LOCATIONS = frozenset({"/", "trajectory"})
MAX_ERROR_REASON_LENGTH = 128
MAX_ERROR_LOCATION_LENGTH = 512


def _safe_error_fields(reason: str, location: str) -> bool:
    if (
        type(reason) is not str
        or len(reason) > MAX_ERROR_REASON_LENGTH
        or _REASON_TOKEN.fullmatch(reason) is None
    ):
        return False
    if type(location) is not str or len(location) > MAX_ERROR_LOCATION_LENGTH:
        return False
    if not location or location in _LEGACY_LOCATIONS:
        return True
    if not location.startswith("/"):
        return False
    return all(
        _LOCATION_SEGMENT.fullmatch(segment) is not None
        and _INVALID_POINTER_ESCAPE.search(segment) is None
        for segment in location.split("/")[1:]
    )


class EvidenceError(ValueError):
    """Only adapter-owned codes, reasons and structural locations reach stderr."""

    def __init__(
        self, code: str, reason: str, location: str = "", context: str = "default"
    ) -> None:
        if not _safe_error_fields(reason, location):
            code, reason, location, context = "INTERNAL_ERROR", "INTERNAL_ERROR", "", "default"
        projection = evidence_projection(code, context)
        self.exit_code = projection.exit_code
        self.payload: dict[str, object] = {
            "schema": "evidence_error.v1",
            "code": code,
            "reason": reason,
            "location": location,
            "next_action": projection.next_action,
            "retryable": False,
            "exit_code": self.exit_code,
        }
        super().__init__(reason)


def usage_error() -> EvidenceError:
    """Return the CLI's fixed, content-minimized argument refusal."""
    return EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP")


def output_limit_error() -> EvidenceError:
    """Return the CLI's fixed, content-minimized output limit refusal."""
    return EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")


class EvidenceArgumentParser(argparse.ArgumentParser):
    """Argument parser whose failures never repeat caller-supplied values."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        raise usage_error()


def read_validated(
    path: Path, schema: str, location: str, max_bytes: int, max_depth: int
) -> tuple[dict[str, Any], str]:
    """Read one bounded object against a code-selected installed contract."""
    document, digest = read_document(
        path, max_bytes, max_depth, location, "INVALID_INPUT", EvidenceError
    )
    validate(packaged_validator(schema), document, EvidenceError, "INVALID_INPUT", location)
    return document, digest


def emit_error(error: EvidenceError, *, as_json: bool) -> int:
    """Emit one fixed diagnostic without caller-controlled error details."""
    encoded = canonical_bytes(error.payload).decode("utf-8")
    output = encoded if as_json else "Evidence command could not complete: " + encoded
    print(output, file=sys.stderr)
    return error.exit_code


def run_cli(
    command: str,
    argv: list[str],
    configure: Callable[[argparse.ArgumentParser], None],
    produce: Callable[[argparse.Namespace], dict[str, object]],
    result_schema: str | ReportOutput,
    output_max_bytes: int = REPORT_MAX_BYTES,
) -> int:
    """Emit a complete report once, or one typed error with no stdout prefix."""
    parser = EvidenceArgumentParser(prog=f"underwrite {command}")
    specification = (
        ReportOutput(result_schema, output_max_bytes)
        if isinstance(result_schema, str)
        else result_schema
    )
    try:
        configure(parser)
        args = parser.parse_args(argv)
        schema = (
            specification.schema(args) if callable(specification.schema) else specification.schema
        )
        choices: dict[str, Validator] | None = None
        if isinstance(schema, tuple):
            if (
                not schema
                or any(type(name) is not str or not name for name in schema)
                or len(set(schema)) != len(schema)
            ):
                raise SchemaConfigurationError("INVALID_OUTPUT_SCHEMA_SET")
            choices = {name: packaged_validator(name) for name in schema}
        report = produce(args)
        if choices is not None:
            selected = report.get("schema")
            if type(selected) is not str or selected not in choices:
                raise EvidenceError("INTERNAL_ERROR", "UNDECLARED_OUTPUT_SCHEMA", "/schema")
            validator = choices[selected]
        else:
            assert isinstance(schema, str)
            validator = packaged_validator(schema)
        validate(validator, report, EvidenceError, "INTERNAL_ERROR")
        output = (
            canonical_bytes(report).decode("utf-8")
            if args.json
            else specification.human(report)
            if specification.human is not None
            else "Completed evidence computation; no approval or independent provenance.\n"
            + json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2)
        )
        if output_line_size(output) > specification.max_bytes:
            raise output_limit_error()
        exit_code = (
            specification.exit_status(report) if specification.exit_status is not None else 0
        )
    except EvidenceError as exc:
        return emit_error(exc, as_json="--json" in argv)
    except SchemaConfigurationError:
        return emit_error(
            EvidenceError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT"),
            as_json="--json" in argv,
        )
    except Exception:
        return emit_error(
            EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR"), as_json="--json" in argv
        )
    print(output)
    return exit_code
