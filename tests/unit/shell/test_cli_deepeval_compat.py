"""Omitted DeepEval versions select local reader policy without claiming a release."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import digest_bytes

from underwrite.cli import record_deepeval
from underwrite.cli.main import main
from underwrite.instrument.ingest import deepeval_compat
from underwrite.instrument.ingest.deepeval_accounting import (
    audit_deepeval_bytes,
    audit_deepeval_file,
)
from underwrite.instrument.ingest.deepeval_trace import (
    audit_deepeval_trace_bytes,
    audit_deepeval_trace_file,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service.records import read_record, read_record_file, verify_record

ROOT = repo_root(Path(__file__).resolve())
ORDINARY = ROOT / "fixtures/golden/external/deepeval/test_run_20260914_132634.json"
TRACE = ROOT / "fixtures/golden/external/deepeval-4.2.8/traces/recovery.json"
NATIVE_ORDINARY = ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json"
NATIVE_TRACE = ROOT / "fixtures/examples/deepeval-auto-4.3.1/recovery.json"
ERROR_EXIT = 2
STORAGE_EXIT = 3


@pytest.mark.parametrize(
    ("source", "options", "schema"),
    [
        (ORDINARY, (), "deepeval_accounting.v3"),
        (TRACE, ("--view", "trace-index"), "deepeval_trace_index.v2"),
    ],
)
def test_omitted_version_selects_auto_result_without_producer_release(
    source: Path,
    options: tuple[str, ...],
    schema: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["audit-deepeval", str(source), *options, "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    report = json.loads(streams.out)
    assert report["schema"] == schema
    assert report["source"]["producer_version"] is None
    assert report["source"]["producer_version_state"] == "unknown"
    assert report["source"]["claims_verified"] is False


@pytest.mark.parametrize(
    ("source", "view"),
    [
        (ORDINARY, "accounting"),
        (NATIVE_ORDINARY, "accounting"),
        (TRACE, "trace-index"),
        (NATIVE_TRACE, "trace-index"),
    ],
)
def test_auto_file_and_bytes_api_agree_on_original_source_identity(source: Path, view: str) -> None:
    raw = source.read_bytes()
    audit = audit_deepeval_bytes if view == "accounting" else audit_deepeval_trace_bytes
    report = audit(raw)
    selected_file = audit_deepeval_file if view == "accounting" else audit_deepeval_trace_file
    assert selected_file(source) == report
    assert report["raw_hash"] == digest_bytes(raw)
    assert cast(dict[str, object], report["source"])["producer_version"] is None
    if view == "accounting":
        assert cast(dict[str, object], report["scope"])["planned_inventory"] == "unknown"


@pytest.mark.parametrize(
    "case",
    [
        (NATIVE_ORDINARY, "accounting", "deepeval_record.v3", "analysis/accounting.json"),
        (NATIVE_TRACE, "trace-index", "deepeval_trace_record.v2", "analysis/trace-index.json"),
    ],
)
def test_auto_record_captures_once_and_recovers_original_bytes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[Path, str, str, str],
) -> None:
    source, view, schema, member = case
    path, store = tmp_path / "selected.json", tmp_path / "store"
    original_raw = source.read_bytes()
    path.write_bytes(original_raw)
    real_read = record_deepeval.read_deepeval_file
    reads: list[Path] = []

    def replace_after_capture(selected: Path) -> bytes:
        reads.append(selected)
        captured = real_read(selected)
        selected.write_bytes(b"changed after capture")
        return captured

    monkeypatch.setattr(record_deepeval, "read_deepeval_file", replace_after_capture)
    args = ["records", "audit-deepeval", str(path), "--root", str(store), "--id", "sample"]
    if view == "trace-index":
        args.extend(("--view", view))
    assert main([*args, "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == "" and reads == [path]
    report = json.loads(streams.out)
    assert report["schema"] == schema and "source_format_version" not in report
    assert report["analysis"]["state"] == "result"
    assert report["analysis"]["report"]["raw_hash"] == digest_bytes(original_raw)
    assert read_record_file(store, "sample", "source/deepeval.bin").data == original_raw
    assert (
        json.loads(read_record_file(store, "sample", member).data) == report["analysis"]["report"]
    )
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert (
        invocation["arguments"]["reader_profile"] == report["analysis"]["report"]["reader_profile"]
    )
    assert "format_version" not in invocation["arguments"]
    metadata = cast(dict[str, Any], read_record(store, "sample").manifest["metadata"])
    assert metadata["selection_mode"] == "auto"
    assert metadata["reader_profile"] == report["analysis"]["report"]["reader_profile"]
    assert "format_version" not in metadata
    path.unlink()
    assert verify_record(store, "sample")["valid"] is True
    assert read_record_file(store, "sample", "source/deepeval.bin").data == original_raw


@pytest.mark.parametrize("marker", ["case", "metric"])
def test_expectation_marker_refuses_ordinary_and_saves_same_private_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], marker: str
) -> None:
    payload = json.loads(NATIVE_ORDINARY.read_bytes())
    case = payload["testCases"][0]
    selected = case if marker == "case" else case["metricsData"][0]
    selected["expectationsData"] = {"PRIVATE_CANARY": "source-only"}
    raw = json.dumps(payload).encode()
    path, store = tmp_path / "selected.json", tmp_path / "store"
    path.write_bytes(raw)
    assert main(["audit-deepeval", str(path), "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and "PRIVATE_CANARY" not in streams.err
    error = json.loads(streams.err)
    assert error["reason"] == "DEEPEVAL_EXPECTATIONS_UNSUPPORTED"
    assert error["location"] == (
        "/testCases/0/expectationsData"
        if marker == "case"
        else "/testCases/0/metricsData/0/expectationsData"
    )
    assert (
        main(
            [
                "records",
                "audit-deepeval",
                str(path),
                "--root",
                str(store),
                "--id",
                "sample",
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    report = json.loads(capsys.readouterr().out)
    assert report["analysis"]["phase"] == "analysis"
    assert report["analysis"]["error"]["reason"] == error["reason"]
    assert read_record_file(store, "sample", "source/deepeval.bin").data == raw
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert "reader_profile" not in invocation["arguments"]


def test_auto_byte_limit_and_missing_bound_inputs_keep_independent_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = NATIVE_ORDINARY.read_bytes()
    monkeypatch.setattr(deepeval_compat, "DEFAULT_MAX_BYTES", len(raw) - 1)
    with pytest.raises(deepeval_compat.DeepevalFileError) as caught:
        audit_deepeval_bytes(raw)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INPUT_LIMIT_EXCEEDED",
        "BYTE_LIMIT_EXCEEDED",
        "/file",
    )
    source, store = tmp_path / "missing-private", tmp_path / "store"
    assert (
        main(
            [
                "records",
                "audit-deepeval",
                str(source),
                "--root",
                str(store),
                "--id",
                "sample",
                "--bindings",
                str(source),
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    streams = capsys.readouterr()
    assert streams.err == ""
    report = json.loads(streams.out)
    assert report["schema"] == "deepeval_bound_record.v2"
    assert report["analysis"]["phase"] == "acquisition"
    assert report["sources"]["deepeval"]["state"] == "unavailable"
    assert report["sources"]["bindings"]["state"] == "unavailable"
    assert report["storage"]["state"] == "saved"
    assert main(["audit-deepeval", str(source), "--bindings", str(source), "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["location"] == "/bindings"


def test_unavailable_auto_source_saves_acquisition_error_without_applied_reader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = tmp_path / "missing", tmp_path / "store"
    assert (
        main(
            [
                "records",
                "audit-deepeval",
                str(path),
                "--root",
                str(store),
                "--id",
                "sample",
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    report = json.loads(capsys.readouterr().out)
    assert report["source"] == {"state": "unavailable"}
    assert report["analysis"]["phase"] == "acquisition"
    assert report["storage"]["state"] == "saved"
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert "reader_profile" not in invocation["arguments"]
    metadata = cast(dict[str, Any], read_record(store, "sample").manifest["metadata"])
    assert metadata["selection_mode"] == "auto" and "reader_profile" not in metadata


def test_trace_index_keeps_steps_but_does_not_interpret_expectations() -> None:
    payload = json.loads(NATIVE_TRACE.read_bytes())
    baseline = audit_deepeval_trace_bytes(NATIVE_TRACE.read_bytes())
    payload["testCases"][0]["expectationsData"] = {"PRIVATE_CANARY": "uninterpreted"}
    report = audit_deepeval_trace_bytes(json.dumps(payload).encode())
    assert (
        cast(dict[str, object], report["counts"])["spans"]
        == cast(dict[str, object], baseline["counts"])["spans"]
    )
    limitations = cast(list[str], report["limitations"])
    assert "EXPECTATIONS_NOT_ANALYZED" in limitations
    assert "SOURCE_CASE_SUCCESS_UNINTERPRETED" in limitations
    assert "PRIVATE_CANARY" not in json.dumps(report)


def test_selected_auto_record_contract_precedes_source_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = record_deepeval.packaged_validator

    def broken(name: str) -> Any:
        if name == record_deepeval.AUTO_RECORD_SCHEMA:
            raise SchemaConfigurationError("PRIVATE_SCHEMA_PATH")
        return original(name)

    def forbidden(_: Path) -> bytes:
        pytest.fail("source read before selected contract")

    monkeypatch.setattr(record_deepeval, "packaged_validator", broken)
    monkeypatch.setattr(record_deepeval, "read_deepeval_file", forbidden)
    store = tmp_path / "store"
    assert (
        main(
            [
                "records",
                "audit-deepeval",
                str(tmp_path / "missing"),
                "--root",
                str(store),
                "--id",
                "sample",
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    streams = capsys.readouterr()
    assert streams.out == "" and not store.exists()
    assert json.loads(streams.err)["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert "PRIVATE_SCHEMA_PATH" not in streams.err


def test_auto_record_output_admission_and_existing_id_refuse_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = NATIVE_ORDINARY, tmp_path / "store"
    args = [
        "records",
        "audit-deepeval",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    ]
    monkeypatch.setattr(record_deepeval, "OUTPUT_MAX_BYTES", 1)
    assert main(args) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and not store.exists()
    assert json.loads(streams.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"
    monkeypatch.undo()
    assert main(args) == 0
    capsys.readouterr()
    assert main(args) == STORAGE_EXIT
    report = json.loads(capsys.readouterr().out)
    assert report["analysis"]["state"] == "result"
    assert report["storage"]["error"]["code"] == "RECORD_EXISTS"


def test_auto_ordinary_human_and_record_human_state_selected_population_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["audit-deepeval", str(NATIVE_ORDINARY)]) == 0
    direct = capsys.readouterr()
    assert direct.err == ""
    assert "Only supplied serialized metricsData rows are counted" in direct.out
    assert "expectation population is unknown" in direct.out
    assert (
        main(
            [
                "records",
                "audit-deepeval",
                str(NATIVE_ORDINARY),
                "--root",
                str(tmp_path / "store"),
                "--id",
                "sample",
            ]
        )
        == 0
    )
    retained = capsys.readouterr()
    assert retained.err == ""
    assert "Only supplied serialized metricsData rows are counted" in retained.out
    assert "expectation population is unknown" in retained.out


def test_auto_trace_human_withholds_expectation_payload_and_interpretation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = json.loads(NATIVE_TRACE.read_bytes())
    payload["testCases"][0]["expectationsData"] = {"PRIVATE_CANARY": "uninterpreted"}
    source = tmp_path / "trace.json"
    source.write_text(json.dumps(payload))
    assert main(["audit-deepeval", str(source), "--view", "trace-index"]) == 0
    streams = capsys.readouterr()
    assert streams.err == "" and "PRIVATE_CANARY" not in streams.out
    assert "Metrics and expectation outcomes are not analyzed" in streams.out
    assert "source case success may include unexamined evaluations" in streams.out
    assert (
        main(
            [
                "records",
                "audit-deepeval",
                str(source),
                "--view",
                "trace-index",
                "--root",
                str(tmp_path / "store"),
                "--id",
                "sample",
            ]
        )
        == 0
    )
    retained = capsys.readouterr()
    assert retained.err == "" and "PRIVATE_CANARY" not in retained.out
    assert "Metrics and expectation outcomes are not analyzed" in retained.out
    assert "source case success may include unexamined evaluations" in retained.out


@pytest.mark.parametrize("version", ["4.1.1", "4.2.8"])
def test_retired_version_flag_is_usage_error(
    capsys: pytest.CaptureFixture[str], version: str
) -> None:
    assert main(["audit-deepeval", str(ORDINARY), "--version", version, "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and json.loads(streams.err)["code"] == "USAGE_ERROR"
