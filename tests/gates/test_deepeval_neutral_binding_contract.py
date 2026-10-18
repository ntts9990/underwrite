"""Neutral owner bindings compose installed common-result and record contracts."""

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
from jsonschema import Draft202012Validator, ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.cli.evidence import EvidenceError
from underwrite.cli.inspect_result import inspect_result_file
from underwrite.cli.record_capture import saved_record
from underwrite.cli.record_deepeval_bound import AUTO_SCHEMA as RECORD_SCHEMA
from underwrite.cli.records import record_error_payload
from underwrite.instrument.evidence.deepeval_binding import (
    AUTO_PACK_SCHEMA,
    AUTO_RESULT_SCHEMA,
    bind_deepeval_accounting_auto,
)
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_SCHEMA,
    account_deepeval_compatible,
)
from underwrite.service.records import RecordError

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json"
PACK = ROOT / "fixtures/examples/evidence/synthetic-deepeval.bindings.v2.json"
INSPECTION_SOURCE = ROOT / "fixtures/examples/evidence/common-result-c55.json"
NEW_NAMES = (
    AUTO_PACK_SCHEMA,
    AUTO_RESULT_SCHEMA,
    "common_result_inspection.v2",
    "common_result_inspection_actions.v2",
    RECORD_SCHEMA,
)
OLD_HASHES = {
    "deepeval_metric_bindings.v1": (
        "7d6b32f69c351efabab142f7813ecfa0b3147ef7fe16036db7d510ccfb6e1b4c"
    ),
    "evidence_result.v1": "d36d9179ffdc28f6be43727dc5ed6ff3c006f100f523c5f3a2e8677c5b019631",
    "deepeval_bound_record.v1": "00f4dcc616116bb411893cb187fa556dbd2d141bd346458b468d8d2c99cde7f7",
    "common_result_inspection.v1": (
        "8e4527b045097aa3f91a0d5befe15950b4631bec66ff05b8871f11fba4b26786"
    ),
    "common_result_inspection_actions.v1": (
        "59b9b808911ec400ddfb8d3ada3bf64db7049e6e0737526b98b8a65785920b56"
    ),
}
DIGEST = "sha256:" + "0" * 64


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _schema(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    )


def _validator(name: str) -> Validator:
    source = _schema(name)
    Draft202012Validator.check_schema(source)
    return cast("Validator", Draft202012Validator(source))


def _pack() -> tuple[dict[str, Any], bytes]:
    return cast(dict[str, Any], json.loads(PACK.read_bytes())), PACK.read_bytes()


def _result() -> tuple[dict[str, object], bytes, bytes]:
    pack, pack_raw = _pack()
    source_raw = SOURCE.read_bytes()
    accounting = {
        "schema": ACCOUNTING_SCHEMA,
        "raw_hash": digest_bytes(source_raw),
        **account_deepeval_compatible(json.loads(source_raw)),
    }
    return (
        bind_deepeval_accounting_auto(accounting, pack, pack_raw_hash=digest_bytes(pack_raw)),
        source_raw,
        pack_raw,
    )


def test_generated_installed_contracts_and_legacy_bytes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_deepeval_neutral_binding.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    source_includes = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"][
        "build-backend"
    ]["source-include"]
    for name in NEW_NAMES:
        authored = ROOT / "contracts" / f"{name}.schema.json"
        packaged = ROOT / "src/underwrite/_contracts" / authored.name
        assert packaged.is_symlink()
        assert packaged.readlink() == Path("../../../contracts") / authored.name
        assert packaged.read_bytes() == authored.read_bytes()
        assert f"contracts/{authored.name}" in source_includes
        _validator(name)
    for name, digest in OLD_HASHES.items():
        assert (
            hashlib.sha256((ROOT / "contracts" / f"{name}.schema.json").read_bytes()).hexdigest()
            == digest
        )
    old_action = subprocess.run(
        [sys.executable, "scripts/build_common_result_inspection_actions.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert old_action.returncode == 0, old_action.stdout + old_action.stderr


def test_pack_requires_one_authority_declaration_without_reader_setup() -> None:
    pack, _ = _pack()
    validator = _validator(AUTO_PACK_SCHEMA)
    validator.validate(pack)
    for mutation in (
        {**pack, "applicable_reader_profiles": ["private"]},
        {**pack, "source": {"format": "deepeval.test-run", "format_version": "4.2.8"}},
        {**pack, "name_binding_authority": "verified"},
    ):
        with pytest.raises(ValidationError):
            validator.validate(mutation)
    with pytest.raises(ValidationError):
        _validator("deepeval_metric_bindings.v1").validate(pack)


def test_result_requires_separate_source_and_declaration_identities() -> None:
    report, source_raw, pack_raw = _result()
    validator = _validator(AUTO_RESULT_SCHEMA)
    validator.validate(report)
    assert cast(dict[str, Any], report["source"])["identity"]["raw_hash"] == digest_bytes(
        source_raw
    )
    declaration = cast(dict[str, Any], report["declaration_source"])
    assert declaration["raw_hash"] == digest_bytes(pack_raw)
    assert cast(dict[str, Any], report["source"])["reader_profile"] is not None
    for field, value in (
        ("declaration_source", None),
        ("source", {**cast(dict[str, Any], report["source"]), "reader_profile": None}),
    ):
        # Generic v2 permits these states; successful DeepEval binding must not emit them.
        forged = deepcopy(report)
        forged[field] = value
        validator.validate(forged)
        assert forged != report
    invalid = deepcopy(report)
    cast(dict[str, Any], invalid["source"])["producer_version"] = "4.3.1"
    with pytest.raises(ValidationError):
        validator.validate(invalid)
    declared = deepcopy(report)
    cast(dict[str, Any], declared["source"]).update(
        {"producer_version": "4.3.1", "producer_version_state": "declared"}
    )
    validator.validate(declared)
    cast(dict[str, Any], declared["source"])["producer_version"] = None
    with pytest.raises(ValidationError):
        validator.validate(declared)


def test_v2_inspection_contracts_derive_v1_shape_and_v2_vocabulary() -> None:
    owner = _schema(AUTO_RESULT_SCHEMA)
    for name, old_name in (
        ("common_result_inspection.v2", "common_result_inspection.v1"),
        ("common_result_inspection_actions.v2", "common_result_inspection_actions.v1"),
    ):
        contract = _schema(name)
        old = _schema(old_name)
        assert contract["properties"]["declared_processing"] == owner["properties"]["processing"]
        assert contract["properties"]["selected_result"]["properties"]["format"] == {
            "const": AUTO_RESULT_SCHEMA
        }
        for key in old["properties"]:
            if key not in {"schema", "selected_result"}:
                assert contract["properties"][key] == old["properties"][key]
    old_report = inspect_result_file(INSPECTION_SOURCE, view="actions")
    for name, old_name in (
        ("common_result_inspection.v2", "common_result_inspection.v1"),
        ("common_result_inspection_actions.v2", "common_result_inspection_actions.v1"),
    ):
        report = deepcopy(old_report)
        if name.endswith("inspection.v2"):
            report.pop("item_next_action_counts")
            report.pop("declared_semantic_binding_counts")
        report["schema"] = name
        cast(dict[str, Any], report["selected_result"])["format"] = AUTO_RESULT_SCHEMA
        _validator(name).validate(report)
        with pytest.raises(ValidationError):
            _validator(old_name).validate(report)


def test_two_source_v2_record_keeps_four_storage_outcomes_without_version_claim() -> None:
    report, source_raw, pack_raw = _result()
    validator = _validator(RECORD_SCHEMA)
    captured = {
        "bindings": {
            "state": "captured",
            "raw_hash": digest_bytes(pack_raw),
            "bytes": len(pack_raw),
        },
        "deepeval": {
            "state": "captured",
            "raw_hash": digest_bytes(source_raw),
            "bytes": len(source_raw),
        },
    }
    envelope: dict[str, Any] = {
        "schema": RECORD_SCHEMA,
        "command": "records audit-deepeval",
        "sources": captured,
        "analysis": {"state": "result", "report": report},
        "storage": saved_record("neutral-bound", DIGEST, 4),
        "exit_code": 0,
    }
    validator.validate(envelope)
    for mutation in ("null-declaration", "wrong-declaration", "null-reader", "unknown-reader"):
        missing_binding = deepcopy(envelope)
        bound = cast(dict[str, Any], missing_binding["analysis"]["report"])
        if mutation == "null-declaration":
            bound["declaration_source"] = None
        elif mutation == "wrong-declaration":
            cast(dict[str, Any], bound["declaration_source"])["format"] = "other"
        else:
            cast(dict[str, Any], bound["source"])["reader_profile"] = (
                None if mutation == "null-reader" else "PRIVATE_READER"
            )
        with pytest.raises(ValidationError):
            validator.validate(missing_binding)
    with pytest.raises(ValidationError):
        validator.validate({**envelope, "source_format_version": "4.2.8"})
    error = EvidenceError("INVALID_INPUT", "INVALID_JSON_INPUT", "/bindings").payload
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
            "sources": {
                "bindings": {"state": "unavailable", "error": error},
                "deepeval": captured["deepeval"],
            },
            "analysis": {"state": "error", "phase": "acquisition", "error": error},
            "storage": saved_record("neutral-bound", DIGEST, 3),
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
