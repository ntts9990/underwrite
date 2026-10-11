"""Bounded CLI adapter for private immutable local record bundles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn, cast

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.local_json import read_document, validate
from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service.records import (
    RecordError,
    RecordSnapshot,
    list_records,
    read_record,
    save_record,
    verify_record,
)

SPEC_MAX_BYTES = 256 * 1024
SPEC_MAX_DEPTH = 16
OUTPUT_MAX_BYTES = 2 * 1024 * 1024

_ERRORS: dict[str, tuple[str, str]] = {
    "INVALID_SPEC": ("INVALID_RECORD_SPEC", "CORRECT_RECORD_SPEC"),
    "INVALID_RECORD": ("INVALID_RECORD_MANIFEST", "SELECT_VALID_RECORD"),
    "INVALID_STORE_LAYOUT": ("INVALID_STORE_LAYOUT", "SELECT_VALID_STORE"),
    "INSECURE_STORE": ("INSECURE_STORE_PERMISSIONS", "SECURE_STORE"),
    "STORE_NOT_FOUND": ("STORE_NOT_FOUND", "CREATE_OR_SELECT_STORE"),
    "RECORD_NOT_FOUND": ("RECORD_NOT_FOUND", "SELECT_EXISTING_RECORD"),
    "RECORD_EXISTS": ("RECORD_ALREADY_EXISTS", "USE_NEW_RECORD_ID"),
    "RECORD_CAPTURE_INCOMPLETE": ("RECORD_CAPTURE_INCOMPLETE", "USE_NEW_RECORD_ID"),
    "RECORD_NOT_PUBLISHED": ("RECORD_NOT_PUBLISHED", "SELECT_PUBLISHED_RECORD"),
    "SOURCE_READ_FAILED": ("SOURCE_READ_FAILED", "CHECK_SOURCE_FILE"),
    "INPUT_LIMIT_EXCEEDED": ("INPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
    "RECORD_LIMIT_EXCEEDED": ("RECORD_LIMIT_EXCEEDED", "REDUCE_RECORD_COUNT"),
    "STORE_IO_ERROR": ("STORE_IO_ERROR", "CHECK_STORE_IO"),
    "UNSUPPORTED_PLATFORM": ("UNSUPPORTED_PLATFORM", NEXT_ACTIONS["UNSUPPORTED_PLATFORM"]),
    "USAGE_ERROR": ("INVALID_ARGUMENTS_SEE_HELP", NEXT_ACTIONS["USAGE_ERROR"]),
    "SCHEMA_CONFIGURATION_ERROR": (
        "INVALID_PACKAGED_CONTRACT",
        NEXT_ACTIONS["SCHEMA_CONFIGURATION_ERROR"],
    ),
    "OUTPUT_LIMIT_EXCEEDED": ("OUTPUT_LIMIT_EXCEEDED", "REDUCE_OUTPUT_SIZE"),
    "INTERNAL_ERROR": ("INTERNAL_ERROR", NEXT_ACTIONS["INTERNAL_ERROR"]),
}
_SERVICE_LOCATIONS = frozenset(
    {"", "/spec", "/spec/files", "/spec/links", "/record", "/record_id", "/catalog"}
)


class _CliError(ValueError):
    def __init__(self, code: str, location: str = "") -> None:
        reason, next_action = _ERRORS[code]
        self.payload: dict[str, object] = {
            "schema": "record_error.v1",
            "code": code,
            "reason": reason,
            "location": location,
            "next_action": next_action,
            "retryable": False,
            "exit_code": 2,
        }
        super().__init__(code)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _CliError("USAGE_ERROR")


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", required=True, type=Path, help="Explicit private record root.")
    parser.add_argument("--json", action="store_true", help="Canonical typed JSON on stdout.")


def configure(parser: argparse.ArgumentParser) -> None:
    """Register the records command family on an existing parser."""
    parser.description = (
        "Capture and inspect selected local files as immutable private bundles. "
        "Storage integrity is not source provenance, reproduction, or approval."
    )
    commands = parser.add_subparsers(dest="records_command", required=True)
    save = commands.add_parser("save", help="Capture the files declared by record_spec.v1.")
    _common(save)
    save.add_argument("--spec", required=True, type=Path, help="Local record_spec.v1 JSON.")
    listing = commands.add_parser("list", help="List complete and unpublished record IDs.")
    _common(listing)
    show = commands.add_parser("show", help="Show one complete published record.")
    _common(show)
    show.add_argument("--id", required=True, help="Record ID.")
    verify = commands.add_parser("verify", help="Verify stored bytes and direct links.")
    _common(verify)
    verify.add_argument("--id", required=True, help="Record ID.")


def _input_error(code: str, reason: str, location: str) -> Exception:
    del reason
    return _CliError(code, location)


def _service_error(error: RecordError) -> _CliError:
    code = getattr(error, "code", None)
    location = getattr(error, "location", None)
    if type(code) is not str or code not in _ERRORS:
        return _CliError("INTERNAL_ERROR")
    if type(location) is not str or location not in _SERVICE_LOCATIONS:
        return _CliError("INTERNAL_ERROR")
    return _CliError(code, location)


def _spec(path: Path) -> dict[str, object]:
    document, _ = read_document(
        path,
        SPEC_MAX_BYTES,
        SPEC_MAX_DEPTH,
        "/spec",
        "INVALID_SPEC",
        _input_error,
    )
    validate(
        packaged_validator("record_spec.v1"),
        document,
        _input_error,
        "INVALID_SPEC",
        "/spec",
    )
    return cast(dict[str, object], document)


def _view(snapshot: RecordSnapshot) -> dict[str, object]:
    validate(
        packaged_validator("result_record.v1"),
        snapshot.manifest,
        _input_error,
        "INTERNAL_ERROR",
        "/output",
    )
    return {
        "schema": "record_view.v1",
        "manifest_digest": snapshot.digest,
        "record": snapshot.manifest,
    }


def _produce(args: argparse.Namespace) -> tuple[dict[str, object], str]:
    root = cast(Path, args.root)
    command = cast(str, args.records_command)
    if command == "save":
        spec_path = cast(Path, args.spec)
        return _view(save_record(root, _spec(spec_path), spec_path.parent)), "record_view.v1"
    if command == "show":
        return _view(read_record(root, cast(str, args.id))), "record_view.v1"
    if command == "list":
        return list_records(root), "record_catalog.v1"
    return verify_record(root, cast(str, args.id)), "record_verification.v1"


def _validated_report(args: argparse.Namespace) -> dict[str, object]:
    report, schema = _produce(args)
    validate(packaged_validator(schema), report, _input_error, "INTERNAL_ERROR", "/output")
    return report


def _render(report: dict[str, object], *, as_json: bool) -> str:
    if as_json:
        return canonical_bytes(report).decode("utf-8")
    return (
        "Record command completed; storage integrity is not source provenance or approval.\n"
        + json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2)
    )


def _emit_error(error: _CliError, *, as_json: bool) -> int:
    encoded = canonical_bytes(error.payload).decode("utf-8")
    output = encoded if as_json else "Record command failed: " + encoded
    sys.stderr.write(output + "\n")
    return 2


def main(argv: list[str]) -> int:
    parser = _Parser(prog="underwrite records", allow_abbrev=False)
    configure(parser)
    try:
        args = parser.parse_args(argv)
        report = _validated_report(args)
        output = _render(report, as_json=cast(bool, args.json))
        if len(output.encode("utf-8")) + 1 > OUTPUT_MAX_BYTES:
            raise _CliError("OUTPUT_LIMIT_EXCEEDED", "/output")
    except RecordError as exc:
        error = _service_error(exc)
    except _CliError as exc:
        error = exc
    except SchemaConfigurationError:
        error = _CliError("SCHEMA_CONFIGURATION_ERROR")
    except Exception:
        error = _CliError("INTERNAL_ERROR")
    else:
        sys.stdout.write(output + "\n")
        return 0
    return _emit_error(error, as_json="--json" in argv)
