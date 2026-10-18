"""Explicit DeepEval profiles preserve old bytes and retain new-profile diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes

from underwrite.cli import record_deepeval
from underwrite.cli.main import main
from underwrite.instrument.ingest.deepeval_accounting import audit_deepeval_bytes
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service.records import read_record, read_record_file

FIXTURES = repo_root(Path(__file__).resolve()) / "fixtures/golden/external"
ERROR_EXIT = 2
STORAGE_EXIT = 3


def record_args(path: Path, store: Path, version: str) -> list[str]:
    return [
        "records",
        "audit-deepeval",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--version",
        version,
        "--json",
    ]


def result(code: int, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    output = capsys.readouterr()
    assert not (output.out and output.err)
    assert code in {0, ERROR_EXIT, STORAGE_EXIT}
    return json.loads(output.out or output.err)


@pytest.mark.parametrize(
    "name", ["ordinary", "flaky_metric", "flaky_case", "score_without_verdict"]
)
def test_actual_new_cli_and_retained_recovery(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    path, store = tmp_path / "input", tmp_path / "store"
    raw = (FIXTURES / "deepeval-4.2.8" / (name + ".json")).read_bytes()
    path.write_bytes(raw)
    code = main(["audit-deepeval", str(path), "--version", "4.2.8", "--json"])
    report = result(code, capsys)
    assert code == 0 and report == audit_deepeval_bytes(raw, version="4.2.8")
    code = main(record_args(path, store, "4.2.8"))
    retained = result(code, capsys)
    assert code == 0 and retained["schema"] == "deepeval_record.v2"
    assert retained["source_format_version"] == "4.2.8"
    assert retained["analysis"]["report"] == report
    assert read_record_file(store, "sample", "source/deepeval.bin").data == raw
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["format_version"] == "4.2.8"
    assert (
        cast(dict[str, object], read_record(store, "sample").manifest["metadata"])["format_version"]
        == "4.2.8"
    )
    path.unlink()
    code = main(
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "sample",
            "--name",
            "analysis/accounting.json",
        ]
    )
    recovered = result(code, capsys)
    assert code == 0 and recovered["content"] == report
    assert (
        main(
            [
                "audit-deepeval",
                str(FIXTURES / "deepeval-4.2.8" / (name + ".json")),
                "--version",
                "4.1.1",
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    capsys.readouterr()


@pytest.mark.parametrize("available", [True, False])
def test_new_diagnostics_identify_selected_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], available: bool
) -> None:
    path, store = tmp_path / "input", tmp_path / "store"
    if available:
        path.write_bytes(b"broken")
    code = main(record_args(path, store, "4.2.8"))
    report = result(code, capsys)
    assert code == ERROR_EXIT and report["source_format_version"] == "4.2.8"
    assert report["analysis"]["state"] == "error"
    assert report["analysis"]["phase"] == ("analysis" if available else "acquisition")
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["format_version"] == "4.2.8"
    if available:
        assert read_record_file(store, "sample", "source/deepeval.bin").data == b"broken"


def test_new_conflict_keeps_analysis_and_old_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = FIXTURES / "deepeval-4.2.8/score_without_verdict.json"
    store = tmp_path / "store"
    assert main(record_args(path, store, "4.2.8")) == 0
    capsys.readouterr()
    before = read_record(store, "sample")
    code = main(record_args(path, store, "4.2.8"))
    report = result(code, capsys)
    assert code == STORAGE_EXIT and report["analysis"]["state"] == "result"
    assert report["storage"]["state"] == "error" and read_record(store, "sample") == before


def test_explicit_old_cli_bytes_and_invocation_preserved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = FIXTURES / "deepeval/test_run_20260914_132634.json"
    assert main(["audit-deepeval", str(path), "--version", "4.1.1", "--json"]) == 0
    explicit = capsys.readouterr().out
    assert json.loads(explicit) == audit_deepeval_bytes(path.read_bytes(), version="4.1.1")
    store = tmp_path / "store"
    report = record_deepeval.run_record_deepeval(path, store, "sample", version="4.1.1")
    assert report["schema"] == "deepeval_record.v1" and "source_format_version" not in report
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert "format_version" not in invocation["arguments"]
    assert read_record_file(store, "sample", "analysis/accounting.json").data == explicit.encode()


def test_unknown_version_has_no_read_or_storage_effect(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(_: Path) -> bytes:
        pytest.fail("source read before version refusal")

    monkeypatch.setattr(record_deepeval, "read_deepeval_file", forbidden)
    path, store = tmp_path / "private", tmp_path / "store"
    with pytest.raises(record_deepeval.EvidenceError) as caught:
        record_deepeval.run_record_deepeval(path, store, "sample", version="private/version")
    assert caught.value.payload["code"] == "UNSUPPORTED_FORMAT"
    assert "private/version" not in canonical_bytes(caught.value.payload).decode()
    assert main(record_args(path, store, "private/version")) == ERROR_EXIT
    report = result(ERROR_EXIT, capsys)
    assert report["code"] == "USAGE_ERROR" and not store.exists()


def test_selected_contract_failure_before_acquisition_or_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = record_deepeval.packaged_validator

    def broken(name: str) -> Any:
        if name == "deepeval_record.v2":
            raise SchemaConfigurationError("private diagnostic")
        return original(name)

    def forbidden(_: Path) -> bytes:
        pytest.fail("source read before selected schema validation")

    monkeypatch.setattr(record_deepeval, "packaged_validator", broken)
    monkeypatch.setattr(record_deepeval, "read_deepeval_file", forbidden)
    store = tmp_path / "store"
    code = main(record_args(tmp_path / "input", store, "4.2.8"))
    report = result(code, capsys)
    assert code == ERROR_EXIT and report["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert not store.exists()


def test_new_human_output_keeps_absent_verdict_and_flaky_limits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = FIXTURES / "deepeval-4.2.8/score_without_verdict.json"
    assert main(["audit-deepeval", str(path), "--version", "4.2.8"]) == 0
    output = capsys.readouterr()
    assert not output.err
    assert "absent source verdict: 1" in output.out
    assert "Source flaky declarations:" in output.out
    assert "do not establish CI outcomes or measured instability" in output.out
    assert "SUPPLY_PLANNED_INVENTORY_FOR_MISSINGNESS" in output.out
