"""No-version Promptfoo intake reports SummaryV3 semantics without release inference."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import record_capture
from underwrite.cli import record_promptfoo as record_command
from underwrite.cli.evidence import run_cli
from underwrite.cli.main import main
from underwrite.instrument.evidence.promptfoo_compat import (
    ACCOUNTING_SCHEMA,
    ROW_INDEX_SCHEMA,
    SUMMARY_VERSION,
)
from underwrite.instrument.ingest import accounting as adapter
from underwrite.instrument.ingest.application import DEFAULT_MAX_DEPTH
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service import records
from underwrite.service.records import read_record, read_record_file, verify_record

ROOT = repo_root(Path(__file__).resolve())
FIXTURES = ROOT / "fixtures/golden/external"
CURRENT = FIXTURES / "promptfoo-0.124.1/results.json"
ERROR_EXIT = 2
STORAGE_EXIT = 3


def _invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any], str, str]:
    code = main(list(args))
    streams = capsys.readouterr()
    return code, json.loads(streams.out or streams.err), streams.out, streams.err


@pytest.mark.parametrize(
    ("name", "producer_version"),
    [("promptfoo/results.json", "0.123.0"), ("promptfoo-0.124.1/results.json", "0.124.1")],
)
def test_no_version_audit_uses_truthful_summary_v3_contract(
    capsys: pytest.CaptureFixture[str], name: str, producer_version: str
) -> None:
    path = FIXTURES / name
    assert main(["audit-promptfoo", str(path), "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    report: dict[str, Any] = json.loads(streams.out)
    assert streams.out.encode() == canonical_bytes(report) + b"\n"
    assert report["schema"] == ACCOUNTING_SCHEMA
    assert report["source"]["summary_version"] == SUMMARY_VERSION
    assert report["source"]["producer_version"] == producer_version
    assert report["source"]["producer_version_state"] == "declared"
    assert report["source"]["claims_verified"] is False
    assert report["raw_hash"] == digest_bytes(path.read_bytes())
    assert report == adapter.audit_promptfoo_bytes(path.read_bytes())


def test_no_version_row_index_keeps_same_counts_and_withholds_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    full_code, full, _, _ = _invoke(capsys, "audit-promptfoo", str(CURRENT), "--json")
    row_code, row, stdout, stderr = _invoke(
        capsys, "audit-promptfoo", str(CURRENT), "--view", "row-index", "--json"
    )
    assert full_code == row_code == 0 and stderr == ""
    assert row["schema"] == ROW_INDEX_SCHEMA
    assert row["source"] == full["source"] and row["raw_hash"] == full["raw_hash"]
    for key in ("producer_stats", "row_tallies", "deltas", "declared_relation", "findings"):
        assert row[key] == full[key]
    assert row["scope"]["values"] == "withheld"
    assert row == adapter.audit_promptfoo_row_index_file(CURRENT)
    packaged_validator(ROW_INDEX_SCHEMA).validate(row)  # pyright: ignore[reportUnknownMemberType]
    assert "prompt" not in row["rows"][0]
    assert "response" not in stdout


def test_auto_file_api_reads_once_and_matches_original_bytes_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = adapter.read_promptfoo_file
    reads: list[Path] = []

    def tracked(path: Path) -> bytes:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(adapter, "read_promptfoo_file", tracked)
    assert adapter.audit_promptfoo_file(CURRENT) == adapter.audit_promptfoo_bytes(
        CURRENT.read_bytes()
    )
    assert reads == [CURRENT]


def test_auto_direct_contract_failure_precedes_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(_: str) -> Any:
        raise SchemaConfigurationError("PRIVATE_SENTINEL")

    def forbidden(_: Path) -> bytes:
        pytest.fail("source read before auto contract admission")

    monkeypatch.setattr(adapter, "packaged_validator", missing)
    monkeypatch.setattr(adapter, "read_promptfoo_file", forbidden)
    with pytest.raises(adapter.AccountingCaseError) as caught:
        adapter.audit_promptfoo_file(tmp_path / "PRIVATE_SENTINEL")
    assert (caught.value.code, caught.value.reason) == (
        "SCHEMA_CONFIGURATION_ERROR",
        "INVALID_PACKAGED_CONTRACT",
    )


@pytest.mark.parametrize("metadata", [None, {}, "absent"])
def test_unknown_declaration_is_explicit_without_guessing_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], metadata: object
) -> None:
    source = json.loads(CURRENT.read_bytes())
    if metadata == "absent":
        source.pop("metadata", None)
    else:
        source["metadata"] = metadata
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    code, report, _, stderr = _invoke(capsys, "audit-promptfoo", str(path), "--json")
    assert code == 0 and stderr == ""
    assert report["source"]["producer_version"] is None
    assert report["source"]["producer_version_state"] == "unknown"
    assert report["reader_profile"] == "promptfoo.summary-v3.selected-fields.v1"


def test_well_formed_future_declaration_and_additive_fields_are_not_release_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = json.loads(CURRENT.read_bytes())
    source["metadata"]["promptfooVersion"] = "999.0.0-beta.2+build.07"
    source["metadata"]["futurePrivateField"] = "PRIVATE_SENTINEL"
    source["results"]["futureAggregate"] = {"private": "PRIVATE_SENTINEL"}
    source["results"]["results"][0]["futureField"] = "PRIVATE_SENTINEL"
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    code, report, stdout, stderr = _invoke(capsys, "audit-promptfoo", str(path), "--json")
    assert code == 0 and stderr == ""
    assert report["source"]["producer_version"] == "999.0.0-beta.2+build.07"
    assert report["source"]["producer_version_state"] == "declared"
    assert report["raw_hash"] == digest_bytes(path.read_bytes())
    assert "PRIVATE_SENTINEL" not in stdout
    row_code, row, row_stdout, _ = _invoke(
        capsys, "audit-promptfoo", str(path), "--view", "row-index", "--json"
    )
    assert row_code == 0 and row["source"] == report["source"]
    assert "PRIVATE_SENTINEL" not in row_stdout


@pytest.mark.parametrize(
    ("mutation", "code", "location"),
    [
        ("bad-version", "INVALID_INPUT", "/metadata/promptfooVersion"),
        ("bad-metadata", "INVALID_INPUT", "/metadata"),
        ("wrong-generation", "UNSUPPORTED_PROFILE", "/results/version"),
        ("bad-row", "INVALID_INPUT", "/results/results"),
    ],
)
def test_malformed_selected_semantics_refuse_atomically_without_private_echo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    code: str,
    location: str,
) -> None:
    source = json.loads(CURRENT.read_bytes())
    if mutation == "bad-version":
        source["metadata"]["promptfooVersion"] = "PRIVATE_SENTINEL/path"
    elif mutation == "bad-metadata":
        source["metadata"] = "PRIVATE_SENTINEL"
    elif mutation == "wrong-generation":
        source["results"]["version"] = 2
    else:
        source["results"]["results"][0]["failureReason"] = "PRIVATE_SENTINEL"
    path = tmp_path / "PRIVATE_SENTINEL.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    exit_code, error, stdout, stderr = _invoke(capsys, "audit-promptfoo", str(path), "--json")
    assert exit_code == ERROR_EXIT and stdout == ""
    assert (error["code"], error["location"]) == (code, location)
    assert "PRIVATE_SENTINEL" not in stderr


@pytest.mark.parametrize("raw", [b"\xffPRIVATE_SENTINEL", b'{"results":{},"results":{}}'])
def test_auto_strict_json_refuses_without_partial_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes
) -> None:
    path = tmp_path / "PRIVATE_SENTINEL.json"
    path.write_bytes(raw)
    code, error, stdout, stderr = _invoke(capsys, "audit-promptfoo", str(path), "--json")
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INVALID_INPUT" and error["location"] == "/file"
    assert "PRIVATE_SENTINEL" not in stderr


def test_auto_decoder_depth_uses_shared_finite_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "PRIVATE_SENTINEL.json"
    depth = DEFAULT_MAX_DEPTH + 1
    path.write_bytes(b"[" * depth + b"0" + b"]" * depth)
    code, error, stdout, stderr = _invoke(capsys, "audit-promptfoo", str(path), "--json")
    assert code == ERROR_EXIT and stdout == ""
    assert (error["code"], error["reason"], error["location"]) == (
        "INPUT_LIMIT_EXCEEDED",
        "DEPTH_LIMIT_EXCEEDED",
        "/file",
    )
    assert "PRIVATE_SENTINEL" not in stderr


def test_auto_direct_output_limit_is_atomic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from underwrite.cli import audit_promptfoo

    code = run_cli(
        "audit-promptfoo",
        [str(CURRENT), "--json"],
        audit_promptfoo.configure,
        audit_promptfoo.produce,
        ACCOUNTING_SCHEMA,
        output_max_bytes=1,
    )
    streams = capsys.readouterr()
    assert code == ERROR_EXIT and streams.out == ""
    assert json.loads(streams.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"


def test_auto_record_retains_actual_source_and_index_after_input_deletion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    raw = CURRENT.read_bytes()
    path.write_bytes(raw)
    code, record, stdout, stderr = _invoke(
        capsys,
        "records",
        "audit-promptfoo",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    )
    assert code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(record) + b"\n"
    assert record["schema"] == record_command.AUTO_RECORD_SCHEMA
    assert record["source"]["raw_hash"] == digest_bytes(raw)
    report = cast(dict[str, Any], adapter.audit_promptfoo_bytes(raw))
    assert record["analysis"] == {"state": "result", "report": report}
    assert read_record_file(store, "sample", "source/promptfoo.bin").data == raw
    assert json.loads(read_record_file(store, "sample", "analysis/accounting.json").data) == report
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert invocation["arguments"]["reader_profile"] == report["reader_profile"]
    assert invocation["arguments"]["producer_version"] == report["source"]["producer_version"]
    manifest = cast(dict[str, Any], read_record(store, "sample").manifest)
    assert manifest["metadata"]["selection_mode"] == "auto"
    assert verify_record(store, "sample")["valid"] is True
    assert str(path) not in stdout
    path.unlink()
    assert read_record_file(store, "sample", "source/promptfoo.bin").data == raw


def test_auto_record_complete_invalid_generation_saves_diagnostic_without_applied_reader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = json.loads(CURRENT.read_bytes())
    source["results"]["version"] = 2
    raw = json.dumps(source).encode()
    path, store = tmp_path / "source.json", tmp_path / "store"
    path.write_bytes(raw)
    code, record, _, stderr = _invoke(
        capsys,
        "records",
        "audit-promptfoo",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    )
    assert code == ERROR_EXIT and stderr == ""
    assert record["source"]["raw_hash"] == digest_bytes(raw)
    assert record["analysis"]["phase"] == "analysis"
    assert record["storage"]["state"] == "saved"
    assert read_record_file(store, "sample", "source/promptfoo.bin").data == raw
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert "reader_profile" not in invocation["arguments"]
    assert (
        "producer_version"
        not in cast(dict[str, Any], read_record(store, "sample").manifest)["metadata"]
    )


def test_auto_record_unknown_declaration_is_not_a_fabricated_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = json.loads(CURRENT.read_bytes())
    source["metadata"] = None
    path, store = tmp_path / "source.json", tmp_path / "store"
    path.write_text(json.dumps(source), encoding="utf-8")
    code, record, _, stderr = _invoke(
        capsys,
        "records",
        "audit-promptfoo",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    )
    assert code == 0 and stderr == ""
    report = record["analysis"]["report"]
    assert report["source"]["producer_version"] is None
    assert report["source"]["producer_version_state"] == "unknown"
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert "producer_version" not in invocation["arguments"]
    assert invocation["arguments"]["producer_version_state"] == "unknown"
    metadata = cast(dict[str, Any], read_record(store, "sample").manifest)["metadata"]
    assert "producer_version" not in metadata and "format_version" not in metadata


def test_auto_record_unavailable_source_saves_only_acquisition_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = tmp_path / "missing", tmp_path / "store"
    args = (
        "records",
        "audit-promptfoo",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    )
    code, record, _, stderr = _invoke(capsys, *args)
    assert code == ERROR_EXIT and stderr == ""
    assert record["source"] == {"state": "unavailable"}
    assert record["analysis"]["phase"] == "acquisition"
    assert record["storage"]["verification"]["files_checked"] == ERROR_EXIT
    assert not (store / "records/sample/files/source").exists()
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert "reader_profile" not in invocation["arguments"]
    retry_code, retry, _, _ = _invoke(capsys, *args)
    assert retry_code == STORAGE_EXIT and retry["storage"]["error"]["code"] == "RECORD_EXISTS"


def test_auto_record_analyzes_and_stores_only_captured_bytes_after_path_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    original_raw = CURRENT.read_bytes()
    path.write_bytes(original_raw)
    original = record_command.read_promptfoo_file
    reads: list[Path] = []

    def replaced(selected: Path) -> bytes:
        reads.append(selected)
        raw = original(selected)
        selected.write_bytes(b"changed after acquisition")
        return raw

    monkeypatch.setattr(record_command, "read_promptfoo_file", replaced)
    record = cast(dict[str, Any], record_command.run_record_promptfoo(path, store, "sample"))
    assert record["exit_code"] == 0 and reads == [path]
    assert record["analysis"]["report"]["raw_hash"] == digest_bytes(original_raw)
    assert read_record_file(store, "sample", "source/promptfoo.bin").data == original_raw


def test_auto_record_output_admission_precedes_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    path.write_bytes(CURRENT.read_bytes())

    def forbidden(root: Path, spec: records.CapturedRecordSpec) -> records.RecordSnapshot:
        del root, spec
        pytest.fail("storage began before output admission")

    monkeypatch.setattr(record_command, "OUTPUT_MAX_BYTES", 1)
    monkeypatch.setattr(record_capture, "save_captured_record", forbidden)
    code, error, stdout, _ = _invoke(
        capsys,
        "records",
        "audit-promptfoo",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    )
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "OUTPUT_LIMIT_EXCEEDED" and not store.exists()


def test_auto_record_postpublication_failure_does_not_claim_absence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    path.write_bytes(CURRENT.read_bytes())
    original = records._fsync_dir  # pyright: ignore[reportPrivateUsage]

    def fail_after_manifest(directory: Path) -> None:
        if directory == store / "records/sample" and (directory / "manifest.json").exists():
            raise OSError("private postpublication fault")
        original(directory)

    monkeypatch.setattr(records, "_fsync_dir", fail_after_manifest)
    code, record, stdout, stderr = _invoke(
        capsys,
        "records",
        "audit-promptfoo",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    )
    assert code == STORAGE_EXIT and stderr == "" and stdout
    assert record["analysis"]["state"] == "result"
    assert record["storage"]["published_record"] is None
    assert (store / "records/sample/manifest.json").exists()


def test_auto_record_schema_failure_precedes_read_and_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = record_command.packaged_validator

    def missing(name: str) -> Any:
        if name == record_command.AUTO_RECORD_SCHEMA:
            raise SchemaConfigurationError("PRIVATE_SENTINEL")
        return original(name)

    def forbidden(_: Path) -> bytes:
        pytest.fail("read occurred before selected auto record contract")

    monkeypatch.setattr(record_command, "packaged_validator", missing)
    monkeypatch.setattr(record_command, "read_promptfoo_file", forbidden)
    store = tmp_path / "store"
    code, error, stdout, stderr = _invoke(
        capsys,
        "records",
        "audit-promptfoo",
        str(tmp_path / "source"),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    )
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert "PRIVATE_SENTINEL" not in stderr and not store.exists()
