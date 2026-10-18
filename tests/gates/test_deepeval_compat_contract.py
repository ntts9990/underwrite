"""The version-neutral DeepEval contracts retain old meanings and storage rules."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from scripts import build_deepeval_compat
from underwrite.cli.evidence import EvidenceError
from underwrite.cli.record_capture import saved_record
from underwrite.cli.record_deepeval import (
    AUTO_RECORD_SCHEMA,
    AUTO_TRACE_RECORD_SCHEMA,
    COMMAND,
)
from underwrite.cli.records import record_error_payload
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_SCHEMA,
    EMPTY_LIMITATION,
    TRACE_SCHEMA,
    account_deepeval_compatible,
    index_deepeval_compatible,
)
from underwrite.service.records import RecordError

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "fixtures/examples/deepeval-auto-4.3.1"
OLD = ROOT / "fixtures/golden/external/deepeval/test_run_20260914_132634.json"
FIXTURE_SHA256 = {
    "ordinary.json": "7c08d852bc0660810ae6387b7eb858b11675dc9e8ff58d6a975e787134a31316",
    "recovery.json": "b9a90b7a6af901d6ba27d9dcb7afafb88117bcf075b3aa098aeeb31e33ec7894",
}
DIGEST = "sha256:" + "0" * 64


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator(name: str) -> Validator:
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _report(name: str, source: dict[str, object]) -> dict[str, Any]:
    counted = (
        account_deepeval_compatible(source)
        if name == ACCOUNTING_SCHEMA
        else index_deepeval_compatible(source)
    )
    return {"schema": name, "raw_hash": DIGEST, **counted}


def _native(filename: str) -> dict[str, object]:
    return json.loads((EXAMPLES / filename).read_bytes())


def test_current_generation_needs_no_old_or_existing_generated_contracts(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in (
        "build_deepeval_accounting.py",
        "build_deepeval_trace_index.py",
        "build_deepeval_compat.py",
        "_record_schema.py",
        "_schema_compose.py",
    ):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    for name in ("evidence_error", "record_error", "record_verification"):
        filename = f"{name}.v1.schema.json"
        shutil.copyfile(ROOT / "contracts" / filename, contracts / filename)
    expected = {
        f"{name}.schema.json": (ROOT / "contracts" / f"{name}.schema.json").read_bytes()
        for name in (ACCOUNTING_SCHEMA, TRACE_SCHEMA, AUTO_RECORD_SCHEMA, AUTO_TRACE_RECORD_SCHEMA)
    }
    command = [sys.executable, str(scripts / "build_deepeval_compat.py")]
    built = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert built.returncode == 0, built.stdout + built.stderr
    for name, raw in expected.items():
        assert (contracts / name).read_bytes() == raw
    checked = subprocess.run(
        [*command, "--check"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_generated_packaged_contracts_and_original_source_bytes() -> None:
    check = subprocess.run(
        [sys.executable, "scripts/build_deepeval_compat.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    includes = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["build-backend"][
        "source-include"
    ]
    for name in (ACCOUNTING_SCHEMA, TRACE_SCHEMA, AUTO_RECORD_SCHEMA, AUTO_TRACE_RECORD_SCHEMA):
        authored = ROOT / "contracts" / f"{name}.schema.json"
        packaged = ROOT / "src/underwrite/_contracts" / authored.name
        assert packaged.is_symlink()
        assert packaged.read_bytes() == authored.read_bytes()
        assert f"contracts/{authored.name}" in includes
        _validator(name)
    for name, build in (
        (ACCOUNTING_SCHEMA, build_deepeval_compat.build_accounting),
        (TRACE_SCHEMA, build_deepeval_compat.build_trace),
    ):
        assert (ROOT / "contracts" / f"{name}.schema.json").read_bytes() == (
            json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
        ).encode()
    for name, expected in FIXTURE_SHA256.items():
        assert hashlib.sha256((EXAMPLES / name).read_bytes()).hexdigest() == expected


def test_native_auto_reports_and_empty_roster_validate() -> None:
    accounting = _validator(ACCOUNTING_SCHEMA)
    trace = _validator(TRACE_SCHEMA)
    ordinary = _native("ordinary.json")
    accounting.validate(_report(ACCOUNTING_SCHEMA, ordinary))
    accounting.validate(_report(ACCOUNTING_SCHEMA, json.loads(OLD.read_bytes())))
    trace.validate(_report(TRACE_SCHEMA, ordinary))
    trace.validate(_report(TRACE_SCHEMA, _native("recovery.json")))
    empty = _report(ACCOUNTING_SCHEMA, {"testCases": []})
    assert EMPTY_LIMITATION in empty["limitations"]
    accounting.validate(empty)


@pytest.mark.parametrize("field", ["reader_profile", "source", "scope"])
def test_contract_rejects_missing_selected_semantic_identity(field: str) -> None:
    report = _report(ACCOUNTING_SCHEMA, _native("ordinary.json"))
    del report[field]
    with pytest.raises(ValidationError):
        _validator(ACCOUNTING_SCHEMA).validate(report)


def test_accounting_branches_cannot_cross_or_claim_a_release() -> None:
    report = _report(ACCOUNTING_SCHEMA, _native("ordinary.json"))
    validator = _validator(ACCOUNTING_SCHEMA)
    for field, value in (
        ("reader_profile", "not-a-reader"),
        ("reader_profile", "deepeval.metric-no-flaky.selected-fields.v1"),
        ("source", {**report["source"], "producer_version": "4.3.1"}),
    ):
        invalid = deepcopy(report)
        invalid[field] = value
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    empty = _report(ACCOUNTING_SCHEMA, {"testCases": []})
    empty["limitations"].remove(EMPTY_LIMITATION)
    with pytest.raises(ValidationError):
        validator.validate(empty)


def test_trace_contract_rejects_wrong_reader_or_source_authority() -> None:
    report = _report(TRACE_SCHEMA, _native("recovery.json"))
    validator = _validator(TRACE_SCHEMA)
    for field, value in (
        ("reader_profile", "deepeval.metric-flaky.selected-fields.v1"),
        ("source", {**report["source"], "claims_verified": True}),
        ("scope", {**report["scope"], "expectations": "measured"}),
    ):
        invalid = deepcopy(report)
        invalid[field] = value
        with pytest.raises(ValidationError):
            validator.validate(invalid)


@pytest.mark.parametrize(
    ("record_name", "analysis_name", "filename"),
    [
        (AUTO_RECORD_SCHEMA, ACCOUNTING_SCHEMA, "ordinary.json"),
        (AUTO_TRACE_RECORD_SCHEMA, TRACE_SCHEMA, "recovery.json"),
    ],
)
def test_auto_record_envelopes_keep_storage_outcomes_and_no_version_claim(
    record_name: str, analysis_name: str, filename: str
) -> None:
    report = _report(analysis_name, _native(filename))
    validator = _validator(record_name)
    envelope: dict[str, Any] = {
        "schema": record_name,
        "command": COMMAND,
        "source": {
            "state": "captured",
            "raw_hash": DIGEST,
            "bytes": len((EXAMPLES / filename).read_bytes()),
        },
        "analysis": {"state": "result", "report": report},
        "storage": saved_record("auto-capture", DIGEST, 3),
        "exit_code": 0,
    }
    validator.validate(envelope)
    with pytest.raises(ValidationError):
        validator.validate({**envelope, "source_format_version": "4.3.1"})
    error = EvidenceError("INVALID_INPUT", "INVALID_JSON_INPUT", "/file").payload
    validator.validate(
        {
            **envelope,
            "analysis": {"state": "error", "phase": "analysis", "error": error},
            "exit_code": 2,
        }
    )
    validator.validate(
        {
            **envelope,
            "source": {"state": "unavailable"},
            "analysis": {"state": "error", "phase": "acquisition", "error": error},
            "storage": saved_record("auto-capture", DIGEST, 2),
            "exit_code": 2,
        }
    )
    validator.validate(
        {
            **envelope,
            "storage": {
                "state": "error",
                "error": record_error_payload(RecordError("STORE_IO_ERROR")),
                "published_record": None,
            },
            "exit_code": 3,
        }
    )
