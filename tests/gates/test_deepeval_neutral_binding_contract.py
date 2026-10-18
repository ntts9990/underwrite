"""Neutral owner bindings compose installed common-result and record contracts."""

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
from jsonschema import Draft202012Validator, ValidationError
from underwrite_core.canonical import digest_bytes

from scripts import build_deepeval_neutral_binding
from underwrite.cli.evidence import EvidenceError
from underwrite.cli.inspect_result import View, inspect_result_file
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
INSTALLED_NAMES = (
    AUTO_PACK_SCHEMA,
    AUTO_RESULT_SCHEMA,
    "common_result_inspection.v2",
    "common_result_inspection_actions.v2",
    RECORD_SCHEMA,
)
S5_FROZEN_HASHES = {
    "evidence_result.v1": "d36d9179ffdc28f6be43727dc5ed6ff3c006f100f523c5f3a2e8677c5b019631",
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


def test_current_binding_generation_uses_only_authored_current_inputs(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in (
        "build_deepeval_metric_bindings.py",
        "build_deepeval_bound_record.py",
        "build_deepeval_neutral_binding.py",
        "_record_schema.py",
        "_schema_compose.py",
    ):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    authored = (
        "metric_definition.v1",
        AUTO_RESULT_SCHEMA,
        "evidence_error.v1",
        "record_error.v1",
        "record_verification.v1",
    )
    for name in authored:
        filename = f"{name}.schema.json"
        shutil.copyfile(ROOT / "contracts" / filename, contracts / filename)
    expected = {
        f"{name}.schema.json": (ROOT / "contracts" / f"{name}.schema.json").read_bytes()
        for name in (AUTO_PACK_SCHEMA, RECORD_SCHEMA)
    }
    before = {path.name: path.read_bytes() for path in contracts.iterdir()}
    command = [sys.executable, str(scripts / "build_deepeval_neutral_binding.py")]
    built = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert built.returncode == 0, built.stdout + built.stderr
    assert {path.name: path.read_bytes() for path in contracts.iterdir()} == {**before, **expected}
    checked = subprocess.run(
        [*command, "--check"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_generated_targets_authored_owner_and_frozen_inspector_assets() -> None:
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
    for name in INSTALLED_NAMES:
        authored = ROOT / "contracts" / f"{name}.schema.json"
        packaged = ROOT / "src/underwrite/_contracts" / authored.name
        assert packaged.is_symlink()
        assert packaged.readlink() == Path("../../../contracts") / authored.name
        assert packaged.read_bytes() == authored.read_bytes()
        assert f"contracts/{authored.name}" in source_includes
        _validator(name)
    for name, digest in S5_FROZEN_HASHES.items():
        assert (
            hashlib.sha256((ROOT / "contracts" / f"{name}.schema.json").read_bytes()).hexdigest()
            == digest
        )
    for name, build in (
        (AUTO_PACK_SCHEMA, build_deepeval_neutral_binding.build_pack_v2),
        (RECORD_SCHEMA, build_deepeval_neutral_binding.build_record_v2),
    ):
        assert (ROOT / "contracts" / f"{name}.schema.json").read_bytes() == (
            json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
        ).encode()


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


def test_frozen_inspection_contracts_keep_shape_and_actual_input_identity(tmp_path: Path) -> None:
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
    current_path = tmp_path / "current-result.json"
    current_path.write_text(json.dumps(_result()[0]), encoding="utf-8")
    views: tuple[tuple[View, str], ...] = (
        ("summary", "common_result_inspection"),
        ("actions", "common_result_inspection_actions"),
    )
    for path, generation in ((INSPECTION_SOURCE, "v1"), (current_path, "v2")):
        for view, family in views:
            report = inspect_result_file(path, view=view)
            _validator(f"{family}.{generation}").validate(report)
            assert report["selected_result"] == {
                "format": f"evidence_result.{generation}",
                "raw_hash": digest_bytes(path.read_bytes()),
            }


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
