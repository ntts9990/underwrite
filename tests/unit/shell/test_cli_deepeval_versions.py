"""Historical raw inputs use current output; retired version selectors have no effects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root

from underwrite.cli import record_deepeval, record_deepeval_bound
from underwrite.cli.main import main
from underwrite.instrument.ingest import deepeval_accounting, deepeval_binding, deepeval_trace
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service.records import read_record, read_record_file

FIXTURES = repo_root(Path(__file__).resolve()) / "fixtures/golden/external"
ERROR_EXIT = 2
STORAGE_EXIT = 3


def record_args(path: Path, store: Path) -> list[str]:
    return [
        "records",
        "audit-deepeval",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
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
def test_actual_field_era_cli_and_retained_recovery(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    path, store = tmp_path / "input", tmp_path / "store"
    raw = (FIXTURES / "deepeval-4.2.8" / (name + ".json")).read_bytes()
    path.write_bytes(raw)
    code = main(["audit-deepeval", str(path), "--json"])
    report = result(code, capsys)
    assert code == 0 and report == deepeval_accounting.audit_deepeval_bytes(raw)
    code = main(record_args(path, store))
    retained = result(code, capsys)
    assert code == 0 and retained["schema"] == "deepeval_record.v3"
    assert "source_format_version" not in retained
    assert report["source"]["producer_version"] is None
    assert retained["analysis"]["report"] == report
    assert read_record_file(store, "sample", "source/deepeval.bin").data == raw
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    metadata = cast(dict[str, object], read_record(store, "sample").manifest["metadata"])
    for identity in (invocation["arguments"], metadata):
        assert identity["selection_mode"] == "auto"
        assert identity["reader_profile"] == report["reader_profile"]
        assert "format_version" not in identity
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


@pytest.mark.parametrize("available", [True, False])
def test_diagnostics_do_not_invent_source_version_or_applied_reader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], available: bool
) -> None:
    path, store = tmp_path / "input", tmp_path / "store"
    if available:
        path.write_bytes(b"broken")
    code = main(record_args(path, store))
    report = result(code, capsys)
    assert code == ERROR_EXIT and "source_format_version" not in report
    assert report["analysis"]["phase"] == ("analysis" if available else "acquisition")
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert not {"format_version", "reader_profile"} & invocation["arguments"].keys()
    if available:
        assert read_record_file(store, "sample", "source/deepeval.bin").data == b"broken"


def test_conflict_keeps_analysis_and_old_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = FIXTURES / "deepeval-4.2.8/score_without_verdict.json"
    store = tmp_path / "store"
    assert main(record_args(path, store)) == 0
    capsys.readouterr()
    before = read_record(store, "sample")
    code = main(record_args(path, store))
    report = result(code, capsys)
    assert code == STORAGE_EXIT and report["analysis"]["state"] == "result"
    assert report["storage"]["state"] == "error" and read_record(store, "sample") == before


def test_old_raw_source_uses_current_bytes_and_invocation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = FIXTURES / "deepeval/test_run_20260914_132634.json"
    assert main(["audit-deepeval", str(path), "--json"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == deepeval_accounting.audit_deepeval_bytes(path.read_bytes())
    store = tmp_path / "store"
    report = record_deepeval.run_record_deepeval(path, store, "sample")
    assert report["schema"] == "deepeval_record.v3"
    assert read_record_file(store, "sample", "analysis/accounting.json").data == output.encode()


@pytest.mark.parametrize("version", ["4.1.1", "4.2.8", "private/version"])
def test_removed_version_has_no_read_or_storage_effect(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    def forbidden(*args: object) -> bytes:
        pytest.fail("source read before retired selector refusal")

    monkeypatch.setattr(record_deepeval, "read_deepeval_file", forbidden)
    monkeypatch.setattr(record_deepeval_bound, "read_deepeval_file", forbidden)
    path, store = tmp_path / "private", tmp_path / "store"
    for operation, positional in (
        (deepeval_accounting.audit_deepeval_bytes, (b"{}",)),
        (deepeval_accounting.audit_deepeval_file, (path,)),
        (deepeval_trace.audit_deepeval_trace_bytes, (b"{}",)),
        (deepeval_trace.audit_deepeval_trace_file, (path,)),
        (deepeval_binding.audit_deepeval_bound_bytes, (b"{}", b"{}")),
        (deepeval_binding.audit_deepeval_bound_file, (path, path)),
        (deepeval_binding.decode_validate_pack_bytes, (b"{}",)),
        (record_deepeval.run_record_deepeval, (path, store, "sample")),
        (record_deepeval_bound.run_record_bound_deepeval, (path, path, store, "sample")),
    ):
        with pytest.raises(TypeError, match="version"):
            cast(Any, operation)(*positional, version=version)
    for argv in (["audit-deepeval", str(path), "--json"], record_args(path, store)):
        assert main([*argv, "--version", version]) == ERROR_EXIT
        streams = capsys.readouterr()
        assert streams.out == "" and json.loads(streams.err)["code"] == "USAGE_ERROR"
        assert version not in streams.err
    assert not store.exists()


def test_contract_failure_precedes_acquisition_or_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = record_deepeval.packaged_validator

    def broken(name: str) -> Any:
        if name == record_deepeval.AUTO_RECORD_SCHEMA:
            raise SchemaConfigurationError("private diagnostic")
        return original(name)

    def forbidden(_: Path) -> bytes:
        pytest.fail("source read before selected schema validation")

    monkeypatch.setattr(record_deepeval, "packaged_validator", broken)
    monkeypatch.setattr(record_deepeval, "read_deepeval_file", forbidden)
    store = tmp_path / "store"
    code = main(record_args(tmp_path / "input", store))
    report = result(code, capsys)
    assert code == ERROR_EXIT and report["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert not store.exists()


def test_human_output_keeps_absent_verdict_and_flaky_limits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = FIXTURES / "deepeval-4.2.8/score_without_verdict.json"
    assert main(["audit-deepeval", str(path)]) == 0
    output = capsys.readouterr()
    assert not output.err
    assert "absent source verdict: 1" in output.out
    assert "Source flaky declarations:" in output.out
    assert "do not establish CI outcomes or measured instability" in output.out
    assert "SUPPLY_PLANNED_INVENTORY_FOR_MISSINGNESS" in output.out
