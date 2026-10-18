"""Two auto Langfuse branches keep their distinct authored meanings."""

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
from underwrite_core.canonical import digest_bytes

from underwrite.cli.evidence import EvidenceError
from underwrite.cli.record_capture import saved_record
from underwrite.cli.record_langfuse import AUTO_RECORD_SCHEMA, COMMAND
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
OLD_SHA256 = {
    "langfuse_accounting.v1": "7a97dc8092a55b6152afc009a92add551ace88cca54d2c885536c48c396f34a4",
    "langfuse_row_index.v1": "f564a485dff960a9e1b43ae7d359348303bccab946ac2ab3b0365da47f8b30f4",
    "langfuse_cli_accounting.v1": (
        "0608cd2b394a9ba816e31eb81af138018531229ff67a3ded7a9fc8fdeee7f3cc"
    ),
    "langfuse_cli_row_index.v1": "096ccc0cb2281b9e81a7068582c36576927bf6f9859c1fadb2c30acc53bb2291",
    "langfuse_record.v1": "f21cb89b8551e5cfa8d7b2b72344c09a0ec18690760e6e3d4cba5748c425d583",
    "langfuse_cli_record.v1": "42231c0296f90a5cab8cd6cccb935ecf8fb89cedaac52891387fb596e791b2ea",
}


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


def test_generated_packaged_contracts_and_explicit_v1_bytes_are_stable() -> None:
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
    for name in (ACCOUNTING_SCHEMA, ROW_INDEX_SCHEMA, AUTO_RECORD_SCHEMA):
        filename = name + ".schema.json"
        authored = ROOT / "contracts" / filename
        packaged = ROOT / "src/underwrite/_contracts" / filename
        assert packaged.is_symlink()
        assert packaged.readlink() == Path("../../../contracts") / filename
        assert packaged.read_bytes() == authored.read_bytes()
        assert "contracts/" + filename in includes
        _validator(name)
    for name, expected in OLD_SHA256.items():
        assert (
            hashlib.sha256((ROOT / "contracts" / f"{name}.schema.json").read_bytes()).hexdigest()
            == expected
        )


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
