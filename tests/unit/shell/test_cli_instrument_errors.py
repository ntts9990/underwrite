"""CLI diagnostics and stream contracts; library mutation tests live under instrument/."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _instrument_errors import ERROR_CASES
from _repo_paths import repo_root
from underwrite_core.canonical import CanonicalizationError

from underwrite.cli import instrument
from underwrite.instrument.ingest import application
from underwrite.instrument.ingest.project import ProjectionError
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.instrument.ingest.transport import Ingestor

ROOT = repo_root(Path(__file__).resolve())
GOLDEN = ROOT / "fixtures/examples/measurement/bundle.json"
CONFIGURATION_EXIT = 2


@pytest.mark.parametrize("vendor", ["inspect", "langfuse", "otlp-json"])
def test_malformed_external_values_return_typed_cli_errors(tmp_path: Path, vendor: str) -> None:
    external = ROOT / "fixtures/golden/external"
    payload: Any  # Vendor JSON fixtures have distinct shapes, including a root list.
    if vendor == "inspect":
        payload = {"version": 2, "status": []}
        format_name, version, reason = "inspect.eval-log", "0.3.263", "MALFORMED_INSPECT_PAYLOAD"
    elif vendor == "langfuse":
        payload = json.loads((external / "langfuse/scores-2026-09-14T06-40-00.json").read_bytes())
        payload[0]["data_type"] = []
        format_name, version, reason = "langfuse.scores", "4.35.0", "MALFORMED_LANGFUSE_PAYLOAD"
    else:
        payload = json.loads((external / "otlp-json/capture.json").read_bytes())
        payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["startTimeUnixNano"] = "1" * 5000
        format_name, version, reason = "otlp-json.traces", "0.160.0", "MALFORMED_OTLP_JSON_PAYLOAD"
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "underwrite.cli.main",
            "ingest",
            "--format",
            format_name,
            "--version",
            version,
            "--file",
            str(path),
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "schema": "instrument_error.v1",
        "command": "ingest",
        "code": "INVALID_INPUT",
        "reason": reason,
        "exit_code": 1,
    }


def _call() -> int:
    return instrument.main(
        "project",
        [
            "--format",
            "underwrite.evidence-bundle",
            "--version",
            "v1",
            "--file",
            str(GOLDEN),
            "--json",
        ],
    )


@pytest.mark.parametrize(("error", "code", "reason", "exit_code"), ERROR_CASES)
def test_typed_exception_mapping_never_reflects_arbitrary_payload(
    error: Exception,
    code: str,
    reason: str,
    exit_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> Ingestor:
        raise error

    monkeypatch.setattr(instrument, "packaged_ingestor", fail)
    assert _call() == exit_code
    output = capsys.readouterr()
    assert output.out == "" and "secret input" not in output.err
    assert json.loads(output.err) == {
        "schema": "instrument_error.v1",
        "command": "project",
        "code": code,
        "reason": reason,
        "exit_code": exit_code,
    }


@pytest.mark.parametrize(
    "error", [RuntimeError("bug"), ValueError("bug"), ProjectionError("NEW_BUG")]
)
def test_unexpected_programming_errors_are_not_reclassified(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> Ingestor:
        raise error

    monkeypatch.setattr(instrument, "packaged_ingestor", fail)
    with pytest.raises((RuntimeError, ValueError, KeyError)):
        _call()
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_non_numeric_canonical_cause_is_not_numeric_or_not_measured(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> Ingestor:
        raise ProjectionError("INVALID_PROJECTION") from CanonicalizationError("UNSUPPORTED_TYPE")

    monkeypatch.setattr(instrument, "packaged_ingestor", fail)
    assert _call() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["code"] == "INVALID_INPUT"
    assert "not_measured" not in output.err


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("missing", "INVALID_OBSERVATION_SCHEMA"),
        ("malformed", "INVALID_OBSERVATION_SCHEMA"),
        ("wrong-id", "WRONG_OBSERVATION_CONTRACT"),
        ("metaschema", "INVALID_OBSERVATION_SCHEMA"),
        ("nested", "NESTED_SCHEMA_RESOURCE"),
        ("remote", "NON_LOCAL_SCHEMA_REFERENCE"),
        ("dynamic", "NON_LOCAL_SCHEMA_REFERENCE"),
        ("unresolved", "OBSERVATION_SCHEMA_EVALUATION_FAILED"),
        ("flattened", "INVALID_OBSERVATION_SCHEMA"),
    ],
)
def test_resource_corruption_has_typed_failure_without_cwd_fallback(
    tmp_path: Path,
    fault: str,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "package"
    resource = root / "_contracts/observation.v1.schema.json"
    resource.parent.mkdir(parents=True)
    owner = ROOT / "contracts/observation.v1.schema.json"
    document = json.loads(owner.read_bytes())
    modifications = {
        "wrong-id": {"$id": "urn:wrong"},
        "metaschema": {"type": "wrong"},
        "nested": {"$defs": {"x": {"$id": "urn:other"}}},
        "remote": {"$ref": "https://not-fetched.invalid/schema"},
        "dynamic": {"$dynamicRef": "https://not-fetched.invalid/schema"},
        "unresolved": {"$ref": "#/$defs/not-present"},
    }
    document.update(modifications.get(fault, {}))
    if fault != "missing":
        text = json.dumps(document)
        if fault == "malformed":
            text = "{"
        elif fault == "flattened":
            text = "../../../contracts/observation.v1.schema.json"
        resource.write_text(text)
    hostile = tmp_path / "contracts"
    hostile.mkdir()
    (hostile / "observation.v1.schema.json").write_bytes(owner.read_bytes())
    monkeypatch.chdir(tmp_path)

    def package_root(name: str) -> Path:
        assert name == "underwrite"
        return root

    monkeypatch.setattr(application, "files", package_root)
    assert _call() == CONFIGURATION_EXIT
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {
        "schema": "instrument_error.v1",
        "command": "project",
        "code": "SCHEMA_CONFIGURATION_ERROR",
        "reason": reason,
        "exit_code": 2,
    }


def test_resource_factory_catches_materialization_io_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("secret path")

    monkeypatch.setattr(application, "as_file", fail)
    with pytest.raises(SchemaConfigurationError, match="^INVALID_OBSERVATION_SCHEMA$"):
        instrument.packaged_ingestor(max_bytes=1, max_depth=1)


@pytest.mark.parametrize("flag", ["O_NONBLOCK", "O_NOCTTY"])
def test_missing_safe_open_capability_is_a_bounded_typed_unavailable_result(
    flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delattr(os, flag)
    assert _call() == CONFIGURATION_EXIT
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {
        "schema": "instrument_error.v1",
        "command": "project",
        "code": "SOURCE_READ_FAILED",
        "reason": "SOURCE_NONBLOCKING_UNAVAILABLE",
        "exit_code": 2,
    }
