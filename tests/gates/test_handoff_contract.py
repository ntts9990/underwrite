"""Composed output contracts stay standalone and generated from their owners."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["paired_handoff_review", "promptfoo_record"])
def test_generated_handoff_contract_matches_owners(name: str) -> None:
    result = subprocess.run(
        [sys.executable, f"scripts/build_{name}.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", ["paired_handoff_review", "promptfoo_record"])
def test_composite_contract_has_only_resolvable_local_references(name: str) -> None:
    document = json.loads((ROOT / f"contracts/{name}.v1.schema.json").read_bytes())
    Draft202012Validator.check_schema(document)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            mapping = cast(dict[str, Any], value)
            if "$ref" in mapping:
                reference = cast(str, mapping["$ref"])
                assert reference.startswith("#/")
                target = document
                for part in reference[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in mapping.values():
                walk(child)
        elif isinstance(value, list):
            for child in cast(list[object], value):
                walk(child)

    walk(document)


@pytest.mark.parametrize("analysis_error", [False, True])
@pytest.mark.parametrize("storage_error", [False, True])
def test_promptfoo_record_exit_and_capture_states_are_bound(
    analysis_error: bool,
    storage_error: bool,
) -> None:
    from jsonschema.exceptions import ValidationError

    from underwrite.cli.evidence import EvidenceError
    from underwrite.cli.local_json import Validator
    from underwrite.cli.records import record_error_payload
    from underwrite.service.records import RecordError

    schema = json.loads((ROOT / "contracts/promptfoo_record.v1.schema.json").read_bytes())
    validator = cast(Validator, Draft202012Validator(schema))
    report = json.loads((ROOT / "fixtures/examples/evidence/baseline-accounting.json").read_bytes())
    identity = {"record_id": "capture", "manifest_digest": "sha256:" + "0" * 64}
    storage: dict[str, Any] = (
        {
            "state": "error",
            "error": record_error_payload(RecordError("STORE_IO_ERROR")),
            "published_record": None,
        }
        if storage_error
        else {
            "state": "saved",
            **identity,
            "verification": {
                "schema": "record_verification.v1",
                **identity,
                "valid": True,
                "files_checked": 3,
                "links_checked": 0,
                "findings": [],
                "verification_scope": "stored_bytes_and_direct_links",
            },
        }
    )
    analysis: dict[str, Any] = (
        {
            "state": "error",
            "phase": "analysis",
            "error": EvidenceError("INVALID_INPUT", "INVALID_INPUT").payload,
        }
        if analysis_error
        else {"state": "result", "report": report}
    )
    exit_code = 3 if storage_error else 2 if analysis_error else 0
    envelope: dict[str, Any] = {
        "schema": "promptfoo_record.v1",
        "command": "records audit-promptfoo",
        "source": {"state": "captured", "raw_hash": report["raw_hash"], "bytes": 1},
        "analysis": analysis,
        "storage": storage,
        "exit_code": exit_code,
    }
    validator.validate(envelope)
    if not storage_error:
        wrong_storage = {**storage, "verification": {**storage["verification"], "files_checked": 2}}
        with pytest.raises(ValidationError):
            validator.validate({**envelope, "storage": wrong_storage})
    for wrong in {0, 2, 3} - {exit_code}:
        with pytest.raises(ValidationError):
            validator.validate({**envelope, "exit_code": wrong})
    with pytest.raises(ValidationError):
        validator.validate({**envelope, "source": {"state": "unavailable"}})
    if analysis_error:
        acquisition = {
            **envelope,
            "source": {"state": "unavailable"},
            "analysis": {**analysis, "phase": "acquisition"},
        }
        if not storage_error:
            with pytest.raises(ValidationError):
                validator.validate(acquisition)
            acquisition["storage"] = {
                **storage,
                "verification": {**storage["verification"], "files_checked": 2},
            }
        validator.validate(acquisition)
        with pytest.raises(ValidationError):
            validator.validate({**acquisition, "source": envelope["source"]})
