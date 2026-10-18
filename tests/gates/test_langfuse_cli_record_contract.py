"""Both concrete Langfuse record contracts retain their own analysis meaning."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from underwrite.cli.evidence import EvidenceError
from underwrite.cli.record_capture import saved_record
from underwrite.cli.record_langfuse import CLI_RECORD_SCHEMA, COMMAND, SCHEMA
from underwrite.instrument.ingest.langfuse_cli import audit_langfuse_cli_files

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures/examples/langfuse-cli"
OLD_SCHEMA_SHA256 = "f21cb89b8551e5cfa8d7b2b72344c09a0ec18690760e6e3d4cba5748c425d583"
GENERATED_FILE_COUNT = 2


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator(name: str) -> Validator:
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _captured() -> dict[str, object]:
    return {"state": "captured", "raw_hash": "sha256:" + "a" * 64, "bytes": 1}


def _result_envelope() -> dict[str, Any]:
    report = audit_langfuse_cli_files(FIXTURES / "observations.json", FIXTURES / "scores.json")
    return {
        "schema": CLI_RECORD_SCHEMA,
        "command": COMMAND,
        "sources": {role: _captured() for role in ("observations", "scores")},
        "analysis": {"state": "result", "report": report},
        "storage": saved_record("capture", "sha256:" + "0" * 64, GENERATED_FILE_COUNT + 2),
        "exit_code": 0,
    }


def test_builder_keeps_blob_bytes_and_emits_current_cli_contract() -> None:
    old = (ROOT / "contracts" / f"{SCHEMA}.schema.json").read_bytes()
    assert hashlib.sha256(old).hexdigest() == OLD_SCHEMA_SHA256
    result = subprocess.run(
        [sys.executable, "scripts/build_langfuse_record.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _validator(SCHEMA)
    _validator(CLI_RECORD_SCHEMA)
    assert (ROOT / "contracts" / f"{SCHEMA}.schema.json").read_bytes() == old


def test_cli_record_contract_accepts_saved_result_and_acquisition_diagnostic() -> None:
    validator = _validator(CLI_RECORD_SCHEMA)
    result = _result_envelope()
    validator.validate(result)
    error = EvidenceError("SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "/scores").payload
    diagnostic = {
        **result,
        "sources": {
            "observations": _captured(),
            "scores": {"state": "unavailable", "error": error},
        },
        "analysis": {"state": "error", "phase": "acquisition", "error": error},
        "storage": saved_record("capture", "sha256:" + "0" * 64, GENERATED_FILE_COUNT + 1),
        "exit_code": 2,
    }
    validator.validate(diagnostic)


@pytest.mark.parametrize(
    "path,value",
    [
        (("storage", "verification", "files_checked"), GENERATED_FILE_COUNT + 1),
        (("analysis", "report", "schema"), "langfuse_accounting.v1"),
        (("exit_code",), 2),
        (("unrecognized_private_path",), "/private/source"),
    ],
)
def test_cli_record_contract_rejects_false_saved_claims(
    path: tuple[str, ...], value: object
) -> None:
    invalid = deepcopy(_result_envelope())
    selected: Any = invalid
    for field in path[:-1]:
        selected = selected[field]
    selected[path[-1]] = value
    with pytest.raises(ValidationError):
        _validator(CLI_RECORD_SCHEMA).validate(invalid)


def test_cli_record_contract_rejects_analysis_phase_after_failed_capture() -> None:
    invalid = _result_envelope()
    error = EvidenceError("SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "/scores").payload
    invalid["sources"]["scores"] = {"state": "unavailable", "error": error}
    invalid["analysis"] = {"state": "error", "phase": "analysis", "error": error}
    invalid["exit_code"] = 2
    with pytest.raises(ValidationError):
        _validator(CLI_RECORD_SCHEMA).validate(invalid)
