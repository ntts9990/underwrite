"""Bounded CLI adapter for private immutable local record bundles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes

from underwrite.cli.local_json import read_document, validate
from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import DecodeError, decode_json
from underwrite.service._record_errors import ERROR_SPECS
from underwrite.service.records import (
    RecordError,
    RecordSnapshot,
    list_records,
    read_record,
    read_record_file,
    save_record,
    verify_record,
)

SPEC_MAX_BYTES = 256 * 1024
SPEC_MAX_DEPTH = 16
OUTPUT_MAX_BYTES = 2 * 1024 * 1024

_ERRORS: dict[str, tuple[str, str]] = {
    code: (reason, NEXT_ACTIONS[code] if action is None else action)
    for code, (reason, action) in ERROR_SPECS.items()
}
DOCUMENT_MAX_DEPTH = 32

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
    document = commands.add_parser(
        "read-json", help="Read one retained JSON file, verified against its manifest; always JSON."
    )
    _common(document)
    document.add_argument("--id", required=True, help="Record ID.")
    document.add_argument("--name", required=True, help="Exact logical name from manifest.files.")
    verify = commands.add_parser("verify", help="Verify stored bytes and direct links.")
    _common(verify)
    verify.add_argument("--id", required=True, help="Record ID.")
    from underwrite.cli import record_promptfoo

    audit = commands.add_parser(
        "audit-promptfoo", help="Audit and retain one entire Promptfoo file."
    )
    record_promptfoo.configure(audit)
    from underwrite.cli import record_langfuse

    langfuse = commands.add_parser(
        "audit-langfuse", help="Audit and retain two entire Langfuse export files."
    )
    record_langfuse.configure(langfuse)
    from underwrite.cli import record_deepeval

    deepeval = commands.add_parser(
        "audit-deepeval", help="Audit and retain one entire DeepEval TestRun file."
    )
    record_deepeval.configure(deepeval)
    from underwrite.cli import record_measure

    measurement = commands.add_parser(
        "measure", help="Measure and retain native evidence and policy."
    )
    record_measure.configure(measurement)
    from underwrite.cli import record_baseline

    baseline = commands.add_parser(
        "audit-baseline", help="Audit and retain an exact baseline exchange."
    )
    record_baseline.configure(baseline)
    from underwrite.cli import record_trajectory

    trajectory = commands.add_parser(
        "audit-trajectory", help="Audit and retain a complete Goppi paired trajectory."
    )
    record_trajectory.configure(trajectory)
    trajectory_read = commands.add_parser(
        "read-trajectory", help="Reopen a retained trajectory after verifying stored bytes."
    )
    record_trajectory.configure_read(trajectory_read)
    from underwrite.cli import replay_measure

    replay = commands.add_parser(
        "replay-measure", help="Compare retained measurement with current calculation."
    )
    replay_measure.configure(replay)


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


def record_error_payload(error: RecordError) -> dict[str, object]:
    """Reuse the record command's finite diagnostic mapping in composed workflows."""
    return _service_error(error).payload


def record_error_candidates() -> tuple[dict[str, object], ...]:
    """Return finite formatter-owned variants for composed-response admission."""
    return tuple(
        record_error_payload(RecordError(code, location))
        for code in sorted(_ERRORS)
        for location in sorted(_SERVICE_LOCATIONS)
    )


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


def _document(root: Path, identifier: str, name: str) -> dict[str, object]:
    """Embed a parsed representation after checking raw byte identity.

    Card: record-reuse | Paper: record_document.v1 | Stage: storage-read
    Decimal/exponent tokens use existing binary64 decoding, not exact decimal arithmetic.
    """
    selected = read_record_file(root, identifier, name)
    try:
        content = decode_json(selected.data, DOCUMENT_MAX_DEPTH)
        canonical_bytes(content)
    except DecodeError as exc:
        code = (
            "INPUT_LIMIT_EXCEEDED"
            if str(exc) == "DEPTH_LIMIT_EXCEEDED"
            else "UNSUPPORTED_RECORD_CONTENT"
        )
        raise _CliError(code, "/record") from None
    except CanonicalizationError:
        raise _CliError("UNSUPPORTED_RECORD_CONTENT", "/record") from None
    return {
        "schema": "record_document.v1",
        "record_id": selected.record_id,
        "name": selected.name,
        "media_type": selected.media_type,
        "bytes": len(selected.data),
        "sha256": selected.sha256,
        "manifest_digest": selected.manifest_digest,
        "verification_scope": "selected_file_against_manifest",
        "content_representation": "parsed_json",
        "content": content,
    }


def _produce(args: argparse.Namespace) -> tuple[dict[str, object], str]:
    root = cast(Path, args.root)
    command = cast(str, args.records_command)
    if command == "save":
        spec_path = cast(Path, args.spec)
        return _view(save_record(root, _spec(spec_path), spec_path.parent)), "record_view.v1"
    if command == "show":
        return _view(read_record(root, cast(str, args.id))), "record_view.v1"
    if command == "read-json":
        return _document(root, cast(str, args.id), cast(str, args.name)), "record_document.v1"
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


def _capture_command(argv: list[str]) -> int | None:  # noqa: PLR0911 - lazy command dispatch
    if argv and argv[0] == "audit-trajectory":
        from underwrite.cli import record_trajectory

        return record_trajectory.main(argv[1:])
    if argv and argv[0] == "read-trajectory":
        from underwrite.cli import record_trajectory

        return record_trajectory.main_read(argv[1:])
    if argv and argv[0] == "audit-baseline":
        from underwrite.cli import record_baseline

        return record_baseline.main(argv[1:])
    if argv and argv[0] == "replay-measure":
        from underwrite.cli import replay_measure

        return replay_measure.main(argv[1:])
    if argv and argv[0] == "measure":
        from underwrite.cli import record_measure

        return record_measure.main(argv[1:])
    if argv and argv[0] == "audit-deepeval":
        from underwrite.cli import record_deepeval

        return record_deepeval.main(argv[1:])
    if argv and argv[0] == "audit-langfuse":
        from underwrite.cli import record_langfuse

        return record_langfuse.main(argv[1:])
    if argv and argv[0] == "audit-promptfoo":
        from underwrite.cli import record_promptfoo

        return record_promptfoo.main(argv[1:])
    return None


def main(argv: list[str]) -> int:
    captured = _capture_command(argv)
    if captured is not None:
        return captured
    parser = _Parser(prog="underwrite records", allow_abbrev=False)
    configure(parser)
    as_json = "--json" in argv or bool(argv and argv[0] == "read-json")
    try:
        args = parser.parse_args(argv)
        report = _validated_report(args)
        output = _render(
            report, as_json=cast(bool, args.json) or args.records_command == "read-json"
        )
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
    return _emit_error(error, as_json=as_json)
