"""Bounded I/O and diagnostics for the independent evidence pilot commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.local_json import read_document, validate
from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

REPORT_MAX_BYTES = 1024 * 1024
ERROR_ACTIONS = {
    **NEXT_ACTIONS,
    "INVALID_INPUT": "CORRECT_INPUT",
    "INPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
    "OUTPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
    "UNSUPPORTED_PROFILE": "USE_SUPPORTED_PROFILE",
    "UNSUPPORTED_FORMAT": "USE_SUPPORTED_PROFILE",
    "EXPECTED_HASH_MISMATCH": "CHECK_EXPECTED_HASH",
    "INVALID_MANIFEST": "CORRECT_MANIFEST",
    "INVALID_ARTIFACT_PATH": "CORRECT_MANIFEST",
    "COMPUTATION_LIMIT_EXCEEDED": "REDUCE_COMPUTATION",
    "INPUT_BINDING_MISMATCH": "CORRECT_INPUT_BINDING",
}


class EvidenceError(ValueError):
    """Only adapter-owned codes, reasons and structural locations reach stderr."""

    def __init__(
        self, code: str, reason: str, location: str = "", next_action: str | None = None
    ) -> None:
        self.payload: dict[str, object] = {
            "schema": "evidence_error.v1",
            "code": code,
            "reason": reason,
            "location": location,
            "next_action": next_action or ERROR_ACTIONS[code],
            "retryable": False,
            "exit_code": 2,
        }
        super().__init__(reason)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise EvidenceError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP")


def read_validated(
    path: Path, schema: str, location: str, max_bytes: int, max_depth: int
) -> tuple[dict[str, Any], str]:
    """Read one bounded object against a code-selected installed contract."""
    document, digest = read_document(
        path, max_bytes, max_depth, location, "INVALID_INPUT", EvidenceError
    )
    validate(packaged_validator(schema), document, EvidenceError, "INVALID_INPUT", location)
    return document, digest


def _error(error: EvidenceError, as_json: bool) -> int:
    encoded = canonical_bytes(error.payload).decode("utf-8")
    output = encoded if as_json else "Evidence command could not complete: " + encoded
    print(output, file=sys.stderr)
    return 2


def run_cli(
    command: str,
    argv: list[str],
    configure: Callable[[argparse.ArgumentParser], None],
    produce: Callable[[argparse.Namespace], dict[str, object]],
    result_schema: str,
    output_max_bytes: int = REPORT_MAX_BYTES,
) -> int:
    """Emit a complete report once, or one typed error with no stdout prefix."""
    parser = _Parser(prog=f"underwrite {command}", allow_abbrev=False)
    try:
        configure(parser)
        args = parser.parse_args(argv)
        report = produce(args)
        validate(packaged_validator(result_schema), report, EvidenceError, "INTERNAL_ERROR")
        output = (
            canonical_bytes(report).decode("utf-8")
            if args.json
            else "Completed evidence computation; no approval or independent provenance.\n"
            + json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2)
        )
        if len(output.encode("utf-8")) + 1 > output_max_bytes:
            raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    except EvidenceError as exc:
        return _error(exc, "--json" in argv)
    except SchemaConfigurationError:
        return _error(
            EvidenceError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT"),
            "--json" in argv,
        )
    except Exception:
        return _error(EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR"), "--json" in argv)
    print(output)
    return 0
