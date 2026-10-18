"""The trace capture envelope composes existing analysis and storage contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from underwrite.cli.evidence import EvidenceError
from underwrite.cli.record_capture import saved_record
from underwrite.cli.record_deepeval import COMMAND, TRACE_RECORD_SCHEMA
from underwrite.cli.records import record_error_payload
from underwrite.instrument.evidence.deepeval_trace import SCHEMA as TRACE_SCHEMA
from underwrite.instrument.evidence.deepeval_trace import SOURCE_VERSION
from underwrite.instrument.ingest.deepeval_trace import audit_deepeval_trace_bytes
from underwrite.service.records import RecordError

ROOT = Path(__file__).resolve().parents[2]
NAME = f"{TRACE_RECORD_SCHEMA}.schema.json"
FIXTURE = ROOT / "fixtures/golden/external/deepeval-4.2.8/traces/recovery.json"
OLD_CONTRACT_SHA256 = {
    "deepeval_record.v1.schema.json": (
        "e94f25c51a43682ed0ba7d5d9c009f8a48bb0edc465896427083123b84722f32"
    ),
    "deepeval_record.v2.schema.json": (
        "296c3fc49fa57b75bf1eca23027b37374bc8960e29db148f43de338a570d8576"
    ),
}
DIGEST = "sha256:" + "0" * 64


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator() -> Validator:
    schema = json.loads((ROOT / "contracts" / NAME).read_bytes())
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _captured() -> dict[str, object]:
    return {"state": "captured", "raw_hash": DIGEST, "bytes": len(FIXTURE.read_bytes())}


def _error() -> dict[str, object]:
    return EvidenceError("INVALID_INPUT", "INVALID_JSON_INPUT", "/file").payload


def _result() -> dict[str, Any]:
    report = audit_deepeval_trace_bytes(FIXTURE.read_bytes(), version=SOURCE_VERSION)
    return {
        "schema": TRACE_RECORD_SCHEMA,
        "command": COMMAND,
        "source_format_version": SOURCE_VERSION,
        "source": _captured(),
        "analysis": {"state": "result", "report": report},
        "storage": saved_record("trace-capture", DIGEST, 3),
        "exit_code": 0,
    }


def test_generated_contract_is_current_packaged_and_keeps_ordinary_bytes() -> None:
    check = subprocess.run(
        [sys.executable, "scripts/build_deepeval_trace_record.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    authored = ROOT / "contracts" / NAME
    packaged = ROOT / "src/underwrite/_contracts" / NAME
    assert packaged.is_symlink()
    assert packaged.readlink() == Path("../../../contracts") / NAME
    assert packaged.read_bytes() == authored.read_bytes()
    includes = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["build-backend"][
        "source-include"
    ]
    assert "contracts/" + NAME in includes
    _validator()
    for name, expected in OLD_CONTRACT_SHA256.items():
        assert hashlib.sha256((ROOT / "contracts" / name).read_bytes()).hexdigest() == expected


def test_trace_record_contract_accepts_saved_result_and_finite_diagnostics() -> None:
    validator = _validator()
    result = _result()
    assert result["analysis"]["report"]["schema"] == TRACE_SCHEMA
    assert result["analysis"]["report"]["counts"]["spans"]["status_errored"] == 1
    validator.validate(result)

    analysis_error = {
        **result,
        "analysis": {"state": "error", "phase": "analysis", "error": _error()},
        "exit_code": 2,
    }
    validator.validate(analysis_error)
    acquisition_error = {
        **analysis_error,
        "source": {"state": "unavailable"},
        "analysis": {"state": "error", "phase": "acquisition", "error": _error()},
        "storage": saved_record("trace-capture", DIGEST, 2),
    }
    validator.validate(acquisition_error)

    storage_error: dict[str, Any] = {
        **result,
        "storage": {
            "state": "error",
            "error": record_error_payload(RecordError("STORE_IO_ERROR")),
            "published_record": None,
        },
        "exit_code": 3,
    }
    validator.validate(storage_error)
    storage_error["storage"]["published_record"] = {
        "record_id": "trace-capture",
        "manifest_digest": DIGEST,
    }
    validator.validate(storage_error)


@pytest.mark.parametrize(
    "path,value",
    [
        (("source_format_version",), "4.1.1"),
        (("analysis", "report", "schema"), "deepeval_accounting.v1"),
        (("storage", "verification", "files_checked"), 2),
        (("storage", "verification", "valid"), False),
        (("storage", "verification", "verification_scope"), "producer_authenticated"),
        (("exit_code",), 2),
        (("private_source_path",), "/private/source"),
    ],
)
def test_trace_record_contract_refuses_false_claims(path: tuple[str, ...], value: object) -> None:
    invalid = deepcopy(_result())
    selected: Any = invalid
    for field in path[:-1]:
        selected = selected[field]
    selected[path[-1]] = value
    with pytest.raises(ValidationError):
        _validator().validate(invalid)


def test_trace_record_requires_selected_version_and_coherent_error_phase() -> None:
    validator = _validator()
    missing = _result()
    del missing["source_format_version"]
    with pytest.raises(ValidationError):
        validator.validate(missing)

    unavailable_result = _result()
    unavailable_result["source"] = {"state": "unavailable"}
    unavailable_result["storage"] = saved_record("trace-capture", DIGEST, 2)
    with pytest.raises(ValidationError):
        validator.validate(unavailable_result)

    wrong_phase = _result()
    wrong_phase["analysis"] = {"state": "error", "phase": "acquisition", "error": _error()}
    wrong_phase["exit_code"] = 2
    with pytest.raises(ValidationError):
        validator.validate(wrong_phase)
