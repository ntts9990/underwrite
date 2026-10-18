"""Two auto Langfuse branches keep their distinct authored meanings."""

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

from scripts import build_langfuse_compat, build_langfuse_record
from underwrite.cli.evidence import EvidenceError
from underwrite.cli.record_capture import saved_record
from underwrite.cli.record_langfuse import AUTO_RECORD_SCHEMA, COMMAND, ROW_RECORD_SCHEMA
from underwrite.cli.records import record_error_payload
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    BLOB_READER,
    CLI_READER,
    ROW_INDEX_SCHEMA,
    account_langfuse_compatible,
    index_langfuse_compatible,
)
from underwrite.service.records import RecordError

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL = ROOT / "fixtures/golden/external/langfuse"
CLI = ROOT / "fixtures/examples/langfuse-cli"
DIGEST = "sha256:" + "0" * 64


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator(name: str) -> Validator:
    document = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(document)
    return cast("Validator", Draft202012Validator(document))


def _report(family: str, *, row_index: bool = False) -> dict[str, Any]:
    if family == "blob":
        paths = {
            "observations": EXTERNAL / "observations_v2-2026-09-14T06-40-00.json",
            "scores": EXTERNAL / "scores-2026-09-14T06-40-00.json",
        }
    else:
        paths = {role: CLI / f"{role}.json" for role in ("observations", "scores")}
    raw = {role: path.read_bytes() for role, path in paths.items()}
    body = cast(
        dict[str, Any],
        account_langfuse_compatible(json.loads(raw["observations"]), json.loads(raw["scores"])),
    )
    for role in raw:
        body["sources"][role]["raw_hash"] = digest_bytes(raw[role])
    full = {"schema": ACCOUNTING_SCHEMA, **body}
    if row_index:
        return {
            "schema": ROW_INDEX_SCHEMA,
            **index_langfuse_compatible(cast(dict[str, object], full)),
        }
    return full


def _record(family: str) -> dict[str, Any]:
    return {
        "schema": AUTO_RECORD_SCHEMA,
        "command": COMMAND,
        "sources": {
            role: {"state": "captured", "raw_hash": DIGEST, "bytes": 1}
            for role in ("observations", "scores")
        },
        "analysis": {"state": "result", "report": _report(family)},
        "storage": saved_record("capture", DIGEST, 4),
        "exit_code": 0,
    }


def test_current_generation_needs_no_legacy_or_existing_target_files(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in (
        "build_langfuse_accounting.py",
        "build_langfuse_cli_accounting.py",
        "build_langfuse_compat.py",
        "build_langfuse_record.py",
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
        for name in (ACCOUNTING_SCHEMA, ROW_INDEX_SCHEMA, AUTO_RECORD_SCHEMA, ROW_RECORD_SCHEMA)
    }
    command = [sys.executable, str(scripts / "build_langfuse_compat.py")]
    generated = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    for name, raw in expected.items():
        assert (contracts / name).read_bytes() == raw
    checked = subprocess.run(
        [*command, "--check"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_generated_packaged_contracts_are_current() -> None:
    for script in ("build_langfuse_compat.py", "build_langfuse_record.py"):
        check = subprocess.run(
            [sys.executable, "scripts/" + script, "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert check.returncode == 0, check.stdout + check.stderr
    includes = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["build-backend"][
        "source-include"
    ]
    for name in (ACCOUNTING_SCHEMA, ROW_INDEX_SCHEMA, AUTO_RECORD_SCHEMA, ROW_RECORD_SCHEMA):
        filename = name + ".schema.json"
        authored = ROOT / "contracts" / filename
        packaged = ROOT / "src/underwrite/_contracts" / filename
        assert packaged.is_symlink()
        assert packaged.readlink() == Path("../../../contracts") / filename
        assert packaged.read_bytes() == authored.read_bytes()
        assert "contracts/" + filename in includes
        _validator(name)
    for name, build in (
        (ACCOUNTING_SCHEMA, build_langfuse_compat.build_accounting_schema),
        (ROW_INDEX_SCHEMA, build_langfuse_compat.build_row_index_schema),
        (AUTO_RECORD_SCHEMA, build_langfuse_record.build_auto_schema),
        (ROW_RECORD_SCHEMA, build_langfuse_record.build_row_schema),
    ):
        assert (ROOT / "contracts" / f"{name}.schema.json").read_bytes() == (
            json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
        ).encode()


@pytest.mark.parametrize(("family", "reader"), [("blob", BLOB_READER), ("cli", CLI_READER)])
def test_selected_full_and_row_reports_validate_in_distinct_branches(
    family: str, reader: str
) -> None:
    for indexed in (False, True):
        report = _report(family, row_index=indexed)
        _validator(report["schema"]).validate(report)
        assert report["reader_profile"] == reader
        assert all(source["producer_version"] is None for source in report["sources"].values())
        assert all("format_version" not in source for source in report["sources"].values())
        assert report["scope"]["selected_fields_only"] is True
        assert report["scope"]["source_windows"] == "unknown"
        if indexed:
            serialized = json.dumps(report)
            assert "synthetic review" not in serialized
            assert "score-1" not in serialized


@pytest.mark.parametrize("family", ["blob", "cli"])
def test_branch_schema_refuses_cross_family_or_false_source_identity(family: str) -> None:
    report = _report(family)
    validator = _validator(ACCOUNTING_SCHEMA)
    for path, value in (
        (("reader_profile",), CLI_READER if family == "blob" else BLOB_READER),
        (("sources", "scores", "producer_version"), "4.63.0"),
        (("sources", "scores", "producer_version_state"), "declared"),
        (("sources", "scores", "claims_verified"), True),
        (("scope", "source_windows"), "complete"),
        (("private_header",), "PRIVATE_CANARY"),
    ):
        invalid = deepcopy(report)
        selected: Any = invalid
        for step in path[:-1]:
            selected = selected[step]
        selected[path[-1]] = value
        with pytest.raises(ValidationError):
            validator.validate(invalid)


@pytest.mark.parametrize("family", ["blob", "cli"])
@pytest.mark.parametrize("indexed", [False, True])
def test_missing_or_unknown_reader_never_selects_a_branch(family: str, indexed: bool) -> None:
    report = _report(family, row_index=indexed)
    validator = _validator(report["schema"])
    missing = deepcopy(report)
    del missing["reader_profile"]
    with pytest.raises(ValidationError):
        validator.validate(missing)
    unknown = deepcopy(report)
    unknown["reader_profile"] = "PRIVATE_CANARY"
    with pytest.raises(ValidationError):
        validator.validate(unknown)


@pytest.mark.parametrize("family", ["blob", "cli"])
def test_record_contract_retains_two_source_outcome_and_uncertainty_rules(family: str) -> None:
    validator = _validator(AUTO_RECORD_SCHEMA)
    result = _record(family)
    validator.validate(result)
    error = EvidenceError("INVALID_INPUT", "LANGFUSE_AUTO_MIXED_FAMILIES", "/scores").payload
    diagnostic = {
        **result,
        "analysis": {"state": "error", "phase": "analysis", "error": error},
        "exit_code": 2,
    }
    validator.validate(diagnostic)
    unavailable = {
        **diagnostic,
        "sources": {
            "observations": result["sources"]["observations"],
            "scores": {"state": "unavailable", "error": error},
        },
        "analysis": {"state": "error", "phase": "acquisition", "error": error},
        "storage": saved_record("capture", DIGEST, 3),
    }
    validator.validate(unavailable)
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
        "record_id": "capture",
        "manifest_digest": DIGEST,
    }
    validator.validate(storage_error)
    for path, value in (
        (("analysis", "report", "schema"), "langfuse_accounting.v1"),
        (("storage", "verification", "files_checked"), 3),
        (("exit_code",), 2),
    ):
        invalid = deepcopy(result)
        selected: Any = invalid
        for step in path[:-1]:
            selected = selected[step]
        selected[path[-1]] = value
        with pytest.raises(ValidationError):
            validator.validate(invalid)
