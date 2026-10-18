"""Auto Promptfoo contracts reuse authored count, row and record semantics."""

from __future__ import annotations

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
from underwrite_core.canonical import digest_bytes

from underwrite.cli.evidence import EvidenceError
from underwrite.cli.record_capture import saved_record
from underwrite.cli.record_promptfoo import AUTO_RECORD_SCHEMA, COMMAND
from underwrite.cli.records import record_error_payload
from underwrite.instrument.evidence.promptfoo_compat import (
    ACCOUNTING_SCHEMA,
    ROW_INDEX_SCHEMA,
    SUMMARY_VERSION,
    PromptfooCompatibilityError,
    account_promptfoo_compatible,
)
from underwrite.service.records import RecordError

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL = ROOT / "fixtures/golden/external"
DIGEST = "sha256:" + "0" * 64


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator(name: str) -> Validator:
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _report(*, row_index: bool = False, old: bool = False) -> dict[str, Any]:
    folder = "promptfoo" if old else "promptfoo-0.124.1"
    raw = (EXTERNAL / folder / "results.json").read_bytes()
    return cast(
        dict[str, Any],
        {
            "schema": ROW_INDEX_SCHEMA if row_index else ACCOUNTING_SCHEMA,
            "raw_hash": digest_bytes(raw),
            **account_promptfoo_compatible(json.loads(raw), row_index=row_index),
        },
    )


def _record() -> dict[str, Any]:
    return {
        "schema": AUTO_RECORD_SCHEMA,
        "command": COMMAND,
        "source": {"state": "captured", "raw_hash": DIGEST, "bytes": 1},
        "analysis": {"state": "result", "report": _report()},
        "storage": saved_record("capture", DIGEST, 3),
        "exit_code": 0,
    }


def test_generated_packaged_contracts_are_current() -> None:
    check = subprocess.run(
        [sys.executable, "scripts/build_promptfoo_compat.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    includes = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["build-backend"][
        "source-include"
    ]
    for name in (ACCOUNTING_SCHEMA, ROW_INDEX_SCHEMA, AUTO_RECORD_SCHEMA):
        filename = f"{name}.schema.json"
        authored = ROOT / "contracts" / filename
        packaged = ROOT / "src/underwrite/_contracts" / filename
        assert packaged.is_symlink()
        assert packaged.readlink() == Path("../../../contracts") / filename
        assert packaged.read_bytes() == authored.read_bytes()
        assert "contracts/" + filename in includes
        _validator(name)


def test_current_generation_needs_no_historical_or_existing_target_files(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("build_promptfoo_compat.py", "_record_schema.py", "_schema_compose.py"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    for name in ("evidence_error", "record_error", "record_verification"):
        filename = f"{name}.v1.schema.json"
        shutil.copyfile(ROOT / "contracts" / filename, contracts / filename)
    expected = {
        f"{name}.schema.json": (ROOT / "contracts" / f"{name}.schema.json").read_bytes()
        for name in (ACCOUNTING_SCHEMA, ROW_INDEX_SCHEMA, AUTO_RECORD_SCHEMA)
    }
    command = [sys.executable, str(scripts / "build_promptfoo_compat.py")]
    generated = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    for name, raw in expected.items():
        assert (contracts / name).read_bytes() == raw
    checked = subprocess.run(
        [*command, "--check"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("old", [True, False])
def test_public_sanitized_regressions_validate_both_auto_views(old: bool) -> None:
    for indexed in (False, True):
        report = _report(row_index=indexed, old=old)
        _validator(report["schema"]).validate(report)
        assert report["source"]["claims_verified"] is False
        assert report["source"]["summary_version"] == SUMMARY_VERSION
        assert report["scope"]["selected_fields_only"] is True
        assert "observation_digest" not in report


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("0.124.1", True),
        ("99.100.101-alpha.1+build.007", True),
        ("0.0.0", True),
        ("1.2.3-1a+001", True),
        ("1.2.3+" + "a" * 122, True),
        ("01.2.3", False),
        ("1.02.3", False),
        ("1.2.03", False),
        ("1.2.3-01", False),
        ("1.2.3-alpha..1", False),
        ("1.2.3+build..1", False),
        ("PRIVATE_CANARY", False),
        ("/private/PRIVATE_CANARY", False),
        ("1.2.3\nPRIVATE_CANARY", False),
        ("1.2.3\u202e", False),
        ("1.2.3" + "A" * 129, False),
    ],
)
def test_parser_and_generated_schema_agree_on_version_token(value: str, valid: bool) -> None:
    source = json.loads((EXTERNAL / "promptfoo-0.124.1/results.json").read_bytes())
    source["metadata"]["promptfooVersion"] = value
    if valid:
        report = cast(
            dict[str, Any],
            {
                "schema": ACCOUNTING_SCHEMA,
                "raw_hash": DIGEST,
                **account_promptfoo_compatible(source),
            },
        )
        assert report["source"]["producer_version"] == value
        _validator(ACCOUNTING_SCHEMA).validate(report)
        indexed = {
            "schema": ROW_INDEX_SCHEMA,
            "raw_hash": DIGEST,
            **account_promptfoo_compatible(source, row_index=True),
        }
        _validator(ROW_INDEX_SCHEMA).validate(indexed)
    else:
        with pytest.raises(PromptfooCompatibilityError) as caught:
            account_promptfoo_compatible(source)
        assert caught.value.location == "/metadata/promptfooVersion"
        report = _report()
        report["source"]["producer_version"] = value
        with pytest.raises(ValidationError):
            _validator(ACCOUNTING_SCHEMA).validate(report)


def test_unknown_and_declared_states_are_relationally_closed() -> None:
    validator = _validator(ACCOUNTING_SCHEMA)
    declared = _report()
    validator.validate(declared)
    for version, state in ((None, "declared"), ("1.2.3", "unknown")):
        wrong = deepcopy(declared)
        wrong["source"]["producer_version"] = version
        wrong["source"]["producer_version_state"] = state
        with pytest.raises(ValidationError):
            validator.validate(wrong)
    unknown = deepcopy(declared)
    unknown["source"]["producer_version"] = None
    unknown["source"]["producer_version_state"] = "unknown"
    validator.validate(unknown)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source", "summary_version"), 2),
        (("source", "claims_verified"), True),
        (("reader_profile",), "PRIVATE_CANARY"),
        (("scope", "selected_fields_only"), False),
        (("private_response",), "PRIVATE_CANARY"),
        (("rows", 0, "category"), "errors"),
        (("rows", 0, "score"), 1),
    ],
)
def test_index_contract_refuses_false_semantics_and_private_values(
    path: tuple[str | int, ...], value: object
) -> None:
    invalid = deepcopy(_report(row_index=True))
    selected: Any = invalid
    for step in path[:-1]:
        selected = selected[step]
    selected[path[-1]] = value
    with pytest.raises(ValidationError):
        _validator(ROW_INDEX_SCHEMA).validate(invalid)


def test_record_contract_retains_existing_outcome_and_uncertainty_rules() -> None:
    validator = _validator(AUTO_RECORD_SCHEMA)
    result = _record()
    validator.validate(result)
    error = EvidenceError(
        "INVALID_INPUT", "PROMPTFOO_PRODUCER_VERSION_INVALID", "/metadata/promptfooVersion"
    ).payload
    diagnostic = {
        **result,
        "analysis": {"state": "error", "phase": "analysis", "error": error},
        "exit_code": 2,
    }
    validator.validate(diagnostic)
    unavailable = {
        **diagnostic,
        "source": {"state": "unavailable"},
        "analysis": {"state": "error", "phase": "acquisition", "error": error},
        "storage": saved_record("capture", DIGEST, 2),
    }
    validator.validate(unavailable)
    failure: dict[str, Any] = {
        **result,
        "storage": {
            "state": "error",
            "error": record_error_payload(RecordError("STORE_IO_ERROR")),
            "published_record": None,
        },
        "exit_code": 3,
    }
    validator.validate(failure)
    failure["storage"]["published_record"] = {"record_id": "capture", "manifest_digest": DIGEST}
    validator.validate(failure)
    for section, field, value in (
        ("analysis", "report", {"schema": "promptfoo_accounting.v1"}),
        ("storage", "verification", {"files_checked": 2}),
    ):
        wrong = deepcopy(result)
        wrong[section][field].update(value)
        with pytest.raises(ValidationError):
            validator.validate(wrong)
    wrong_phase = deepcopy(result)
    wrong_phase["source"] = {"state": "unavailable"}
    wrong_phase["analysis"] = {"state": "result", "report": _report()}
    wrong_phase["storage"] = saved_record("capture", DIGEST, 2)
    with pytest.raises(ValidationError):
        validator.validate(wrong_phase)
