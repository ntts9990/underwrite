"""A selected DeepEval trace record binds source and index to one captured buffer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import record_capture
from underwrite.cli import record_deepeval as command
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES
from underwrite.instrument.ingest.deepeval_trace import audit_deepeval_trace_bytes
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service import records
from underwrite.service.records import read_record, read_record_file, verify_record

ROOT = repo_root(Path(__file__).resolve())
NATIVE = ROOT / "fixtures/golden/external/deepeval-4.2.8/traces/recovery.json"
ERROR_EXIT = 2
STORAGE_EXIT = 3


def _args(path: Path, store: Path, *options: str) -> list[str]:
    return [
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--version",
        "4.2.8",
        "--view",
        "trace-index",
        *options,
        "--json",
    ]


def _invoke(
    capsys: pytest.CaptureFixture[str], path: Path, store: Path, *options: str
) -> tuple[int, dict[str, Any], str, str]:
    return _invoke_args(capsys, _args(path, store, *options))


def _invoke_args(
    capsys: pytest.CaptureFixture[str], args: list[str]
) -> tuple[int, dict[str, Any], str, str]:
    exit_code = command.main(args)
    streams = capsys.readouterr()
    return exit_code, json.loads(streams.out or streams.err), streams.out, streams.err


def test_native_recovery_source_and_index_are_saved_from_one_buffer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = tmp_path / "input.json", tmp_path / "store"
    raw = NATIVE.read_bytes()
    path.write_bytes(raw)
    exit_code, report, stdout, stderr = _invoke(capsys, path, store)
    assert exit_code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["schema"] == "deepeval_trace_record.v1"
    assert report["source_format_version"] == "4.2.8"
    assert report["source"]["raw_hash"] == digest_bytes(raw)
    index = cast(dict[str, Any], audit_deepeval_trace_bytes(raw, version="4.2.8"))
    assert report["analysis"] == {"state": "result", "report": index}
    assert index["cases"][0]["source_success"] is True
    assert index["counts"]["spans"]["status_errored"] == 1
    assert (
        read_record_file(store, "sample", "source/deepeval.bin", max_bytes=DEFAULT_MAX_BYTES).data
        == raw
    )
    assert json.loads(read_record_file(store, "sample", "analysis/trace-index.json").data) == index
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["view"] == "trace-index"
    assert invocation["arguments"]["format_version"] == "4.2.8"
    assert invocation["arguments"]["rule_profile"] == index["rule_profile"]
    manifest = cast(dict[str, Any], read_record(store, "sample").manifest)
    assert manifest["kind"] == "deepeval.trace-index"
    assert manifest["metadata"]["view"] == "trace-index"
    assert manifest["metadata"]["rule_profile"] == index["rule_profile"]
    assert verify_record(store, "sample")["valid"] is True
    assert str(path) not in stdout and "primary_lookup" not in stdout
    path.unlink()
    assert (
        read_record_file(store, "sample", "source/deepeval.bin", max_bytes=DEFAULT_MAX_BYTES).data
        == raw
    )


@pytest.mark.parametrize("invalid", ["missing-version", "old-version", "bindings", "bad-view"])
def test_invalid_trace_selection_refuses_before_source_and_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    path, store = tmp_path / "PRIVATE_SOURCE", tmp_path / "store"
    args = _args(path, store)
    if invalid == "missing-version":
        args.remove("--version")
        args.remove("4.2.8")
    elif invalid == "old-version":
        args[args.index("--version") + 1] = "4.1.1"
    elif invalid == "bindings":
        args.extend(("--bindings", str(tmp_path / "PRIVATE_BINDINGS")))
    else:
        args[args.index("--view") + 1] = "PRIVATE_VIEW"

    def forbidden(_: Path) -> bytes:
        pytest.fail("invalid selector read the source")

    monkeypatch.setattr(command, "read_deepeval_file", forbidden)
    exit_code, error, stdout, stderr = _invoke_args(capsys, args)
    assert exit_code == ERROR_EXIT and stdout == ""
    assert error["code"] in {"UNSUPPORTED_FORMAT", "USAGE_ERROR"}
    assert "PRIVATE" not in stderr and not store.exists()


def test_direct_api_invalid_view_and_id_refuse_before_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(_: Path) -> bytes:
        pytest.fail("invalid API selection read the source")

    monkeypatch.setattr(command, "read_deepeval_file", forbidden)
    path, store = tmp_path / "source", tmp_path / "store"
    for identifier, view in (("sample", "PRIVATE_VIEW"), ("../bad", "trace-index")):
        with pytest.raises(command.EvidenceError):
            command.run_record_deepeval(path, store, identifier, version="4.2.8", view=view)
    assert not store.exists()


def test_selected_record_contract_failure_precedes_source_read_and_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = command.packaged_validator

    def broken(name: str) -> Any:
        if name == command.TRACE_RECORD_SCHEMA:
            raise SchemaConfigurationError("PRIVATE_SCHEMA_PATH")
        return original(name)

    def forbidden(_: Path) -> bytes:
        pytest.fail("source read before selected record contract")

    monkeypatch.setattr(command, "packaged_validator", broken)
    monkeypatch.setattr(command, "read_deepeval_file", forbidden)
    store = tmp_path / "store"
    exit_code, error, stdout, stderr = _invoke(capsys, tmp_path / "source", store)
    assert exit_code == ERROR_EXIT and stdout == ""
    assert error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert "PRIVATE_SCHEMA_PATH" not in stderr and not store.exists()


@pytest.mark.parametrize("raw", [b"\xffPRIVATE_SOURCE", b"duplicate-span"])
def test_complete_invalid_trace_is_saved_as_diagnostic_with_same_source_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    if raw == b"duplicate-span":
        source = json.loads(NATIVE.read_bytes())
        trace = source["testCases"][0]["trace"]
        trace["toolSpans"][0]["uuid"] = trace["agentSpans"][0]["uuid"]
        raw = json.dumps(source).encode()
    path.write_bytes(raw)
    exit_code, report, stdout, stderr = _invoke(capsys, path, store)
    assert exit_code == report["exit_code"] == ERROR_EXIT and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["source"]["raw_hash"] == digest_bytes(raw)
    assert report["analysis"]["phase"] == "analysis"
    assert report["storage"]["state"] == "saved"
    assert (
        read_record_file(store, "sample", "source/deepeval.bin", max_bytes=DEFAULT_MAX_BYTES).data
        == raw
    )
    assert (
        json.loads(read_record_file(store, "sample", "analysis/error.json").data)
        == (report["analysis"]["error"])
    )
    assert cast(dict[str, Any], read_record(store, "sample").manifest)["metadata"]["view"] == (
        "trace-index"
    )
    assert "PRIVATE_SOURCE" not in stdout


def test_unavailable_source_saves_acquisition_diagnostic_and_consumes_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = tmp_path / "missing", tmp_path / "store"
    exit_code, report, _, stderr = _invoke(capsys, path, store)
    assert exit_code == ERROR_EXIT and stderr == ""
    assert report["source"] == {"state": "unavailable"}
    assert report["analysis"]["phase"] == "acquisition"
    assert report["storage"]["verification"]["files_checked"] == ERROR_EXIT
    assert not (store / "records/sample/files/source/deepeval.bin").exists()
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["view"] == "trace-index"
    retry_exit, retry, _, _ = _invoke(capsys, path, store)
    assert retry_exit == STORAGE_EXIT and retry["storage"]["error"]["code"] == "RECORD_EXISTS"


def test_source_replacement_after_capture_cannot_change_index_or_saved_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    original_raw = NATIVE.read_bytes()
    path.write_bytes(original_raw)
    original = command.read_deepeval_file
    reads: list[Path] = []

    def replace(selected: Path) -> bytes:
        reads.append(selected)
        captured = original(selected)
        selected.write_bytes(b"changed after acquisition")
        return captured

    monkeypatch.setattr(command, "read_deepeval_file", replace)
    report = cast(
        dict[str, Any],
        command.run_record_deepeval(path, store, "sample", version="4.2.8", view="trace-index"),
    )
    assert report["exit_code"] == 0 and reads == [path]
    assert report["analysis"]["report"]["raw_hash"] == digest_bytes(original_raw)
    assert (
        read_record_file(store, "sample", "source/deepeval.bin", max_bytes=DEFAULT_MAX_BYTES).data
        == original_raw
    )


def test_raw_identity_mismatch_refuses_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    raw = NATIVE.read_bytes()
    path.write_bytes(raw)
    real_audit = command.audit_deepeval_trace_bytes

    def mismatched(captured: bytes, *, version: str) -> dict[str, object]:
        report = real_audit(captured, version=version)
        return {**report, "raw_hash": "sha256:" + "0" * 64}

    monkeypatch.setattr(command, "audit_deepeval_trace_bytes", mismatched)
    with pytest.raises(command.EvidenceError) as caught:
        command.run_record_deepeval(path, store, "sample", version="4.2.8", view="trace-index")
    assert caught.value.payload["reason"] == "RAW_IDENTITY_MISMATCH"
    assert not store.exists()


def test_postpublication_storage_error_does_not_claim_absence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    path.write_bytes(NATIVE.read_bytes())
    original = records._fsync_dir  # pyright: ignore[reportPrivateUsage]

    def fail_after_manifest(directory: Path) -> None:
        if directory == store / "records/sample" and (directory / "manifest.json").exists():
            raise OSError("private postpublication fault")
        original(directory)

    monkeypatch.setattr(records, "_fsync_dir", fail_after_manifest)
    exit_code, report, stdout, stderr = _invoke(capsys, path, store)
    assert exit_code == STORAGE_EXIT and stderr == "" and stdout
    assert report["analysis"]["state"] == "result"
    assert report["storage"]["state"] == "error"
    assert report["storage"]["published_record"] is None
    assert (store / "records/sample/manifest.json").exists()


def test_verification_failure_retains_returned_record_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    path.write_bytes(NATIVE.read_bytes())

    def fail_verification(root: Path, identifier: str) -> dict[str, object]:
        del root, identifier
        raise records.RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(record_capture, "verify_record", fail_verification)
    exit_code, report, stdout, stderr = _invoke(capsys, path, store)
    assert exit_code == STORAGE_EXIT and stderr == "" and stdout
    assert report["analysis"]["state"] == "result"
    assert report["storage"]["published_record"] == {
        "record_id": "sample",
        "manifest_digest": read_record(store, "sample").digest,
    }


def test_trace_human_output_is_minimized_and_names_unknown_coverage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    path.write_bytes(NATIVE.read_bytes())
    args = _args(path, store)
    args.remove("--json")
    assert command.main(args) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    assert "source ERRORED spans 1" in streams.out
    assert "instrumentation coverage unknown" in streams.out
    assert "Saved and verified record" in streams.out
    assert "primary_lookup" not in streams.out and str(path) not in streams.out


def test_output_admission_refuses_before_storage_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = tmp_path / "source.json", tmp_path / "store"
    path.write_bytes(NATIVE.read_bytes())

    def forbidden(root: Path, spec: records.CapturedRecordSpec) -> records.RecordSnapshot:
        del root, spec
        pytest.fail("record stored before output admission")

    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", 1)
    monkeypatch.setattr(record_capture, "save_captured_record", forbidden)
    exit_code, error, stdout, _ = _invoke(capsys, path, store)
    assert exit_code == ERROR_EXIT and stdout == ""
    assert error["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert not store.exists()


def test_interruption_propagates_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(_: Path) -> bytes:
        raise KeyboardInterrupt

    monkeypatch.setattr(command, "read_deepeval_file", interrupted)
    store = tmp_path / "store"
    with pytest.raises(KeyboardInterrupt):
        command.run_record_deepeval(
            tmp_path / "source", store, "sample", version="4.2.8", view="trace-index"
        )
    assert not store.exists()
