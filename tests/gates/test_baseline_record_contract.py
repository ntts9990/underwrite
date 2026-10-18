"""Native baseline record composes the exact capture and storage contracts."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _baseline_exchange import build_baseline_exchange
from jsonschema import Draft202012Validator, ValidationError

from scripts import build_baseline_record
from underwrite.cli.records import record_error_payload
from underwrite.instrument.evidence.goppi_baseline import FILES, project_baseline_result
from underwrite.service.records import RecordError

ROOT = Path(__file__).resolve().parents[2]
NAME = "baseline_record.v2.schema.json"
DIGEST = "sha256:" + "0" * 64


def _schema() -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / NAME).read_bytes())


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator() -> Validator:
    return cast("Validator", Draft202012Validator(_schema()))


def _error() -> dict[str, object]:
    return {
        "schema": "evidence_error.v1",
        "code": "INVALID_INPUT",
        "reason": "BASELINE_SHAPE_INVALID",
        "location": "/exchange/export.json",
        "next_action": "CORRECT_INPUT",
        "retryable": False,
        "exit_code": 2,
    }


def _envelope(*, analysis_error: bool = False, storage_error: bool = False) -> dict[str, Any]:
    source_files = build_baseline_exchange().files()
    analysis = (
        {"state": "error", "error": _error()}
        if analysis_error
        else {
            "state": "result",
            "report": project_baseline_result(build_baseline_exchange().documents()),
        }
    )
    storage: dict[str, Any] = (
        {
            "state": "error",
            "error": record_error_payload(RecordError("INTERNAL_ERROR")),
            "published_record": None,
        }
        if storage_error
        else {
            "state": "saved",
            "record_id": "synthetic-baseline",
            "manifest_digest": DIGEST,
            "verification": {
                "schema": "record_verification.v1",
                "record_id": "synthetic-baseline",
                "manifest_digest": DIGEST,
                "valid": True,
                "files_checked": 7,
                "links_checked": 0,
                "findings": [],
                "verification_scope": "stored_bytes_and_direct_links",
            },
        }
    )
    return {
        "schema": "baseline_record.v2",
        "command": "records audit-baseline",
        "source": {
            "state": "captured",
            "files": [
                {"name": name, "raw_hash": DIGEST, "bytes": len(source_files[name])}
                for name in FILES
            ],
        },
        "analysis": analysis,
        "storage": storage,
        "exit_code": 3 if storage_error else 2 if analysis_error else 0,
    }


def _reject(envelope: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _validator().validate(envelope)


def test_generated_contract_is_current_and_packaged() -> None:
    subprocess.run(
        [sys.executable, "scripts/build_baseline_record.py", "--check"], cwd=ROOT, check=True
    )
    packaged = ROOT / "src/underwrite/_contracts" / NAME
    assert packaged.is_symlink()
    assert packaged.readlink() == Path("../../../contracts") / NAME
    assert packaged.read_bytes() == (ROOT / "contracts" / NAME).read_bytes()
    includes = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["build-backend"][
        "source-include"
    ]
    assert "contracts/" + NAME in includes
    Draft202012Validator.check_schema(_schema())
    for owner in ("evidence_result", "evidence_error", "record_error", "record_verification"):
        assert owner in _schema()["$defs"]
    assert (ROOT / "contracts" / NAME).read_bytes() == (
        json.dumps(build_baseline_record.build_schema(), indent=2) + "\n"
    ).encode()


def test_current_generation_needs_no_old_or_existing_baseline_target(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("build_baseline_record.py", "_record_schema.py", "_schema_compose.py"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    for owner in (
        "evidence_result.v2",
        "evidence_error.v1",
        "record_error.v1",
        "record_verification.v1",
    ):
        filename = owner + ".schema.json"
        shutil.copyfile(ROOT / "contracts" / filename, contracts / filename)
    before = {path.name: path.read_bytes() for path in contracts.iterdir()}
    expected = (ROOT / "contracts" / NAME).read_bytes()
    command = [sys.executable, str(scripts / "build_baseline_record.py")]
    built = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert built.returncode == 0, built.stdout + built.stderr
    assert {path.name: path.read_bytes() for path in contracts.iterdir()} == {
        **before,
        NAME: expected,
    }
    checked = subprocess.run(
        [*command, "--check"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("change", ["format", "reader", "producer", "identity", "declaration"])
def test_baseline_capture_rejects_valid_generic_result_with_other_source_claims(
    change: str,
) -> None:
    envelope = _envelope()
    report = envelope["analysis"]["report"]
    if change == "format":
        report["source"]["format"] = "other.producer"
    elif change == "reader":
        report["source"]["reader_profile"] = "other.reader"
    elif change == "producer":
        report["source"].update(producer_version="1.2.3", producer_version_state="declared")
    elif change == "identity":
        report["source"]["identity"] = {"kind": "single_file", "raw_hash": DIGEST}
    else:
        report["declaration_source"] = {
            "format": "owner.pack",
            "raw_hash": DIGEST,
            "authority": "owner_asserted_unverified",
        }
    generic = Draft202012Validator(
        json.loads((ROOT / "contracts/evidence_result.v2.schema.json").read_bytes())
    )
    generic.validate(report)  # pyright: ignore[reportUnknownMemberType]
    _reject(envelope)


def test_baseline_capture_rejects_valid_external_v1_result_without_relabeling() -> None:
    original = ROOT / "fixtures/conformance/goppi-c55-common/evidence-result.json"
    raw = original.read_bytes()
    report = json.loads(raw)
    validator = Draft202012Validator(
        json.loads((ROOT / "contracts/evidence_result.v1.schema.json").read_bytes())
    )
    validator.validate(report)  # pyright: ignore[reportUnknownMemberType]
    assert report["schema"] == "evidence_result.v1"
    envelope = _envelope()
    envelope["analysis"]["report"] = report
    _reject(envelope)
    assert original.read_bytes() == raw


@pytest.mark.parametrize(
    ("analysis_error", "storage_error"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_outcome_branches_and_exit_codes(analysis_error: bool, storage_error: bool) -> None:
    envelope = _envelope(analysis_error=analysis_error, storage_error=storage_error)
    validator = _validator()
    validator.validate(envelope)
    for code in {0, 2, 3} - {envelope["exit_code"]}:
        _reject({**envelope, "exit_code": code})
    if storage_error:
        envelope["storage"]["published_record"] = {
            "record_id": "synthetic-baseline",
            "manifest_digest": DIGEST,
        }
        validator.validate(envelope)


def test_source_requires_exact_ordered_bounded_file_population() -> None:
    envelope = _envelope()
    for files in (
        envelope["source"]["files"][:-1],
        envelope["source"]["files"] + [envelope["source"]["files"][0]],
        list(reversed(envelope["source"]["files"])),
    ):
        changed = copy.deepcopy(envelope)
        changed["source"]["files"] = files
        _reject(changed)
    for key, value in (
        ("name", "other.json"),
        ("raw_hash", "sha256:not-a-digest"),
        ("bytes", -1),
        ("bytes", 4 * 1024 * 1024 + 1),
    ):
        changed = copy.deepcopy(envelope)
        changed["source"]["files"][2][key] = value
        _reject(changed)
    changed = copy.deepcopy(envelope)
    changed["source"]["state"] = "unavailable"
    _reject(changed)


def test_embedded_owners_and_storage_verification_cannot_be_promoted() -> None:
    envelope = _envelope()
    mutations = (
        ("analysis", "report", "schema", "read.v1"),
        ("storage", "verification", "files_checked", 6),
        ("storage", "verification", "valid", False),
        ("storage", "verification", "links_checked", 1),
        ("storage", "verification", "verification_scope", "producer_authenticated"),
    )
    for section, child, key, value in mutations:
        changed = copy.deepcopy(envelope)
        changed[section][child][key] = value
        _reject(changed)
    changed = _envelope(analysis_error=True)
    changed["analysis"]["error"]["exit_code"] = 0
    _reject(changed)
    changed = _envelope(storage_error=True)
    changed["storage"]["error"]["schema"] = "evidence_error.v1"
    _reject(changed)
    changed = _envelope(storage_error=True)
    changed["storage"]["published_record"] = {"record_id": "synthetic-baseline"}
    _reject(changed)
