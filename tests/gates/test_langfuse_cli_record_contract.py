"""Current Langfuse captures bind accounting or rows without legacy report branches."""

from __future__ import annotations

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
from underwrite.cli.record_langfuse import AUTO_RECORD_SCHEMA, COMMAND, ROW_RECORD_SCHEMA
from underwrite.instrument.ingest.langfuse_accounting import (
    audit_langfuse_files,
    audit_langfuse_row_index_files,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures/examples/langfuse-cli"
GENERATED_FILE_COUNT = 2


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator(name: str) -> Validator:
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _captured() -> dict[str, object]:
    return {"state": "captured", "raw_hash": "sha256:" + "a" * 64, "bytes": 1}


def _result_envelope(*, row_index: bool = False) -> dict[str, Any]:
    audit = audit_langfuse_row_index_files if row_index else audit_langfuse_files
    report = audit(FIXTURES / "observations.json", FIXTURES / "scores.json")
    return {
        "schema": ROW_RECORD_SCHEMA if row_index else AUTO_RECORD_SCHEMA,
        "command": COMMAND,
        "sources": {role: _captured() for role in ("observations", "scores")},
        "analysis": {"state": "result", "report": report},
        "storage": saved_record("capture", "sha256:" + "0" * 64, GENERATED_FILE_COUNT + 2),
        "exit_code": 0,
    }


def test_builder_emits_only_current_accounting_and_row_contracts() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_langfuse_record.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _validator(ROW_RECORD_SCHEMA)
    _validator(AUTO_RECORD_SCHEMA)


@pytest.mark.parametrize("row_index", [False, True])
def test_cli_record_contract_accepts_saved_result_and_acquisition_diagnostic(
    row_index: bool,
) -> None:
    result = _result_envelope(row_index=row_index)
    validator = _validator(result["schema"])
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
@pytest.mark.parametrize("row_index", [False, True])
def test_cli_record_contract_rejects_false_saved_claims(
    path: tuple[str, ...], value: object, row_index: bool
) -> None:
    invalid = deepcopy(_result_envelope(row_index=row_index))
    selected: Any = invalid
    for field in path[:-1]:
        selected = selected[field]
    selected[path[-1]] = value
    with pytest.raises(ValidationError):
        _validator(invalid["schema"]).validate(invalid)


@pytest.mark.parametrize("row_index", [False, True])
def test_cli_record_contract_rejects_analysis_phase_after_failed_capture(row_index: bool) -> None:
    invalid = _result_envelope(row_index=row_index)
    error = EvidenceError("SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "/scores").payload
    invalid["sources"]["scores"] = {"state": "unavailable", "error": error}
    invalid["analysis"] = {"state": "error", "phase": "analysis", "error": error}
    invalid["exit_code"] = 2
    with pytest.raises(ValidationError):
        _validator(invalid["schema"]).validate(invalid)


@pytest.mark.parametrize("row_index", [False, True])
def test_current_capture_contract_rejects_the_other_valid_analysis_view(row_index: bool) -> None:
    selected = _result_envelope(row_index=row_index)
    other = _result_envelope(row_index=not row_index)
    _validator(other["schema"]).validate(other)
    selected["analysis"] = other["analysis"]
    with pytest.raises(ValidationError):
        _validator(selected["schema"]).validate(selected)
