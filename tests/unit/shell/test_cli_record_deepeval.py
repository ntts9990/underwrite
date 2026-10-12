"""Fixed in-process accounting capture preserves independent analysis/storage outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import record_capture
from underwrite.cli import record_deepeval as command
from underwrite.cli import records as record_cli
from underwrite.instrument.ingest import deepeval_accounting as adapter
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service import records

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "fixtures/golden/external/deepeval/test_run_20260914_132634.json"
ANALYSIS_ERROR_EXIT = 2
STORAGE_ERROR_EXIT = 3


def invoke(
    path: Path, store: Path, capsys: pytest.CaptureFixture[str], identifier: str = "sample"
) -> tuple[int, dict[str, Any], str, str]:
    code = record_cli.main(
        ["audit-deepeval", str(path), "--root", str(store), "--id", identifier, "--json"]
    )
    streams = capsys.readouterr()
    return code, json.loads(streams.out or streams.err), streams.out, streams.err


def source(tmp_path: Path, raw: bytes | None = None) -> Path:
    path = tmp_path / "input.json"
    path.write_bytes(GOLDEN.read_bytes() if raw is None else raw)
    return path


def stored_files(store: Path, identifier: str = "sample") -> Path:
    return store / "records" / identifier / "files"


def test_saved_result_matches_existing_audit_and_records_actual_composition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = source(tmp_path)
    raw = path.read_bytes()
    store = tmp_path / "store"
    code, report, stdout, stderr = invoke(path, store, capsys)
    assert code == report["exit_code"] == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["analysis"]["report"] == adapter.audit_deepeval_file(path)
    assert report["source"] == {
        "state": "captured",
        "raw_hash": digest_bytes(raw),
        "bytes": len(raw),
    }
    assert report["storage"]["state"] == "saved"
    assert report["storage"]["verification"]["valid"] is True
    files = stored_files(store)
    assert (files / "source/deepeval.bin").read_bytes() == raw
    assert (
        json.loads((files / "analysis/accounting.json").read_bytes())
        == report["analysis"]["report"]
    )
    invocation = json.loads((files / "invocation.json").read_bytes())
    assert invocation["command"] == "records audit-deepeval"
    assert invocation["execution_mode"] == "in_process"
    assert invocation["analysis_exit_code"] == 0
    assert invocation["analysis_exit_code_scope"] == "component_contract_not_os_process"
    assert invocation["aggregate_outcome_location"] == "command_output"
    assert invocation["arguments"] == {
        "source_path_display": str(path),
        "root_display": str(store),
        "record_id": "sample",
    }
    assert not {"argv", "cwd", "stdout", "exit_code", "manifest_digest"} & invocation.keys()
    assert path.read_bytes() == raw
    assert str(path) not in stdout and str(store) not in stdout


@pytest.mark.parametrize("raw", [b"not json", b"\xff", b"{}"])
def test_complete_invalid_bytes_are_saved_with_their_actual_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes
) -> None:
    path = source(tmp_path, raw)
    store = tmp_path / "store"
    code, report, _, stderr = invoke(path, store, capsys)
    assert code == report["exit_code"] == ANALYSIS_ERROR_EXIT and stderr == ""
    assert report["source"]["state"] == "captured"
    assert report["analysis"]["state"] == "error" and report["analysis"]["phase"] == "analysis"
    assert report["storage"]["state"] == "saved"
    files = stored_files(store)
    assert (files / "source/deepeval.bin").read_bytes() == raw
    assert json.loads((files / "analysis/error.json").read_bytes()) == report["analysis"]["error"]
    assert not (files / "analysis/accounting.json").exists()


def test_unavailable_source_saves_diagnostic_consuming_the_record_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = tmp_path / "missing", tmp_path / "store"
    code, report, _, _ = invoke(path, store, capsys)
    assert code == ANALYSIS_ERROR_EXIT
    assert report["source"] == {"state": "unavailable"}
    assert report["analysis"]["phase"] == "acquisition"
    assert report["storage"]["verification"]["files_checked"] == len(
        ("analysis/error.json", "invocation.json")
    )
    assert not (stored_files(store) / "source").exists()
    code, retry, _, _ = invoke(path, store, capsys)
    assert code == retry["exit_code"] == STORAGE_ERROR_EXIT
    assert retry["analysis"] == report["analysis"]
    assert retry["storage"]["error"]["code"] == "RECORD_EXISTS"


def test_input_limit_failure_retains_no_partial_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    monkeypatch.setattr(adapter, "DEFAULT_MAX_BYTES", 1)
    code, report, _, _ = invoke(path, store, capsys)
    assert code == ANALYSIS_ERROR_EXIT
    assert report["source"] == {"state": "unavailable"}
    assert report["analysis"]["error"]["code"] == "INPUT_LIMIT_EXCEEDED"
    assert not (stored_files(store) / "source").exists()


def test_source_mutation_after_capture_does_not_change_saved_or_analyzed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    original_bytes = path.read_bytes()
    original = command.read_deepeval_file
    reads: list[Path] = []

    def mutate(selected: Path) -> bytes:
        reads.append(selected)
        raw = original(selected)
        selected.write_bytes(b"changed after the capture")
        return raw

    monkeypatch.setattr(command, "read_deepeval_file", mutate)
    report = command.run_record_deepeval(path, store, "sample")
    assert report["exit_code"] == 0 and reads == [path]
    analysis = cast(dict[str, Any], report["analysis"])
    assert analysis["report"]["raw_hash"] == digest_bytes(original_bytes)
    assert (stored_files(store) / "source/deepeval.bin").read_bytes() == original_bytes


@pytest.mark.parametrize("invalid_source", [False, True])
def test_storage_failure_retains_success_or_failed_analysis(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    invalid_source: bool,
) -> None:
    path = source(tmp_path, b"bad" if invalid_source else None)
    store = tmp_path / "store"

    def fail(root: Path, spec: records.CapturedRecordSpec) -> records.RecordSnapshot:
        del root, spec
        raise records.RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(record_capture, "save_captured_record", fail)
    code, report, stdout, stderr = invoke(path, store, capsys)
    assert code == report["exit_code"] == STORAGE_ERROR_EXIT and stdout and not stderr
    assert report["analysis"]["state"] == ("error" if invalid_source else "result")
    assert report["storage"]["published_record"] is None
    assert not store.exists()


def test_record_admission_failure_retains_analysis_without_store_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    monkeypatch.setattr(records, "MAX_FILE_BYTES", 1)
    code, report, _, _ = invoke(path, store, capsys)
    assert code == STORAGE_ERROR_EXIT and report["analysis"]["state"] == "result"
    assert report["storage"]["error"]["code"] == "INPUT_LIMIT_EXCEEDED"
    assert not store.exists()


def test_postpublication_failure_remains_error_without_returned_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    original = records._fsync_dir  # pyright: ignore[reportPrivateUsage]

    def fail(directory: Path) -> None:
        if directory == store / "records/sample" and (directory / "manifest.json").exists():
            raise OSError("injected post-publication failure")
        original(directory)

    monkeypatch.setattr(records, "_fsync_dir", fail)
    code, report, _, _ = invoke(path, store, capsys)
    assert code == STORAGE_ERROR_EXIT and report["analysis"]["state"] == "result"
    assert report["storage"]["error"]["code"] == "STORE_IO_ERROR"
    assert report["storage"]["published_record"] is None
    assert (store / "records/sample/manifest.json").exists()


def test_verification_failure_keeps_known_returned_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = source(tmp_path), tmp_path / "store"

    def fail(root: Path, identifier: str) -> dict[str, object]:
        del root, identifier
        raise records.RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(record_capture, "verify_record", fail)
    code, report, _, _ = invoke(path, store, capsys)
    assert code == STORAGE_ERROR_EXIT and report["analysis"]["state"] == "result"
    assert report["storage"]["published_record"] == {
        "record_id": "sample",
        "manifest_digest": records.read_record(store, "sample").digest,
    }


@pytest.mark.parametrize("as_json", [False, True])
def test_output_admission_failure_calls_no_save(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    as_json: bool,
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    called: list[bool] = []
    original = record_capture.save_captured_record

    def track(root: Path, spec: records.CapturedRecordSpec) -> records.RecordSnapshot:
        called.append(True)
        return original(root, spec)

    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", 1)
    monkeypatch.setattr(record_capture, "save_captured_record", track)
    args = [str(path), "--root", str(store), "--id", "sample"] + (["--json"] if as_json else [])
    assert command.main(args) == ANALYSIS_ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and "OUTPUT_LIMIT_EXCEEDED" in streams.err
    assert not called and not store.exists()


def test_packaged_schema_failure_happens_before_read_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(name: str) -> Any:
        del name
        raise SchemaConfigurationError("INVALID_INSPECTION_SCHEMA")

    def forbidden(path: Path) -> bytes:
        del path
        pytest.fail("read started before contracts loaded")

    monkeypatch.setattr(command, "packaged_validator", fail)
    monkeypatch.setattr(command, "read_deepeval_file", forbidden)
    code, error, stdout, _ = invoke(tmp_path / "missing", tmp_path / "store", capsys)
    assert code == ANALYSIS_ERROR_EXIT and stdout == ""
    assert error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert not (tmp_path / "store").exists()


def test_invalid_id_is_rejected_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(path: Path) -> bytes:
        del path
        pytest.fail("invalid ID must not read input")

    monkeypatch.setattr(command, "read_deepeval_file", forbidden)
    with pytest.raises(command.EvidenceError) as caught:
        command.run_record_deepeval(tmp_path / "missing", tmp_path / "store", "../invalid")
    assert caught.value.payload["code"] == "USAGE_ERROR"


def test_undecodable_display_path_is_storage_failure_not_lost_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = GOLDEN.read_bytes()

    def read(path: Path) -> bytes:
        del path
        return raw

    monkeypatch.setattr(command, "read_deepeval_file", read)
    store = tmp_path / "store"
    report = command.run_record_deepeval(Path("private-\ud800"), store, "sample")
    assert report["exit_code"] == STORAGE_ERROR_EXIT
    assert cast(dict[str, object], report["analysis"])["state"] == "result"
    assert cast(dict[str, Any], report["storage"])["error"]["code"] == "INVALID_SPEC"
    assert not store.exists()


def test_bytes_entry_point_keeps_own_input_cap_and_matches_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source(tmp_path)
    raw = path.read_bytes()
    assert adapter.audit_deepeval_bytes(raw) == adapter.audit_deepeval_file(path)
    monkeypatch.setattr(adapter, "DEFAULT_MAX_BYTES", len(raw) - 1)
    with pytest.raises(adapter.DeepevalFileError) as caught:
        adapter.audit_deepeval_bytes(raw)
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


def test_unpublished_reservation_is_not_reused_or_deleted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    command.run_record_deepeval(path, store, "first")
    pending = store / "records/sample"
    pending.mkdir(mode=0o700)
    code, report, _, _ = invoke(path, store, capsys)
    assert code == STORAGE_ERROR_EXIT
    assert report["storage"]["error"]["code"] == "RECORD_CAPTURE_INCOMPLETE"
    assert pending.is_dir() and not list(pending.iterdir())


def test_storage_error_candidates_obey_component_contract_and_exit_digit_bound() -> None:
    validator = packaged_validator("record_error.v1")
    for candidate in record_cli.record_error_candidates():
        validator.validate(candidate)  # pyright: ignore[reportUnknownMemberType]
    assert {
        len(canonical_bytes(code)) for code in (0, ANALYSIS_ERROR_EXIT, STORAGE_ERROR_EXIT)
    } == {1}


@pytest.mark.parametrize("as_json", [True, False])
def test_exact_response_bound_covers_all_storage_branches_and_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json: bool
) -> None:
    path = source(tmp_path)
    report = command.run_record_deepeval(path, tmp_path / "reference", "sample")
    saved = cast(dict[str, Any], report["storage"])
    identity = {"record_id": "sample", "manifest_digest": saved["manifest_digest"]}
    variants = [report]
    for error in record_cli.record_error_candidates():
        for published in (None, identity):
            variants.append(
                {
                    **report,
                    "exit_code": STORAGE_ERROR_EXIT,
                    "storage": {
                        "state": "error",
                        "error": error,
                        "published_record": published,
                    },
                }
            )
    sizes = [
        len(canonical_bytes(item) if as_json else command.render_human(item).encode("utf-8")) + 1
        for item in variants
    ]
    maximum = max(sizes)
    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", maximum)
    actual = command.run_record_deepeval(path, tmp_path / "accepted", "sample", as_json=as_json)
    assert actual["exit_code"] == 0
    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", maximum - 1)
    with pytest.raises(command.EvidenceError) as caught:
        command.run_record_deepeval(path, tmp_path / "refused", "sample", as_json=as_json)
    assert caught.value.payload["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize("delta", [0, -1])
def test_analysis_read_cap_includes_final_newline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    delta: int,
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    report = adapter.audit_deepeval_file(path)
    maximum = len(canonical_bytes(report)) + 1
    monkeypatch.setattr(command, "RECORD_READ_MAX_BYTES", maximum + delta)
    if delta:
        with pytest.raises(command.EvidenceError) as caught:
            command.run_record_deepeval(path, store, "sample")
        assert caught.value.payload["code"] == "OUTPUT_LIMIT_EXCEEDED"
        assert not store.exists()
    else:
        assert command.run_record_deepeval(path, store, "sample")["exit_code"] == 0
        assert len((stored_files(store) / "analysis/accounting.json").read_bytes()) == maximum


def test_original_removal_retains_zero_error_sibling_and_unknown_planned_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    raw = path.read_bytes()
    expected = adapter.audit_deepeval_file(path)
    original = command.read_deepeval_file
    reads: list[Path] = []

    def read(selected: Path) -> bytes:
        reads.append(selected)
        data = original(selected)
        selected.unlink()
        return data

    monkeypatch.setattr(command, "read_deepeval_file", read)
    result = command.run_record_deepeval(path, store, "sample")
    assert reads == [path] and not path.exists()
    analysis = cast(dict[str, Any], result["analysis"])["report"]
    assert analysis == expected
    assert (stored_files(store) / "source/deepeval.bin").read_bytes() == raw
    assert (
        json.loads(records.read_record_file(store, "sample", "analysis/accounting.json").data)
        == expected
    )
    assert analysis["cases"][1]["metrics"][0]["score"] == 0
    assert [row["state"] for row in analysis["cases"][2]["metrics"]] == ["scored", "error"]
    assert analysis["scope"]["planned_inventory"] == "unknown"
    assert analysis["source"]["claims_verified"] is False
    assert cast(dict[str, Any], result["storage"])["verification"]["files_checked"] == len(
        ("source/deepeval.bin", "analysis/accounting.json", "invocation.json")
    )


@pytest.mark.parametrize("phase", ["acquisition", "analysis"])
def test_unexpected_errors_are_finite_and_interrupts_propagate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    path = source(tmp_path)
    for exception in (RuntimeError, KeyboardInterrupt):

        def fail(value: object, error: type[BaseException] = exception) -> Any:
            del value
            raise error("private source text")

        monkeypatch.setattr(
            command,
            "read_deepeval_file" if phase == "acquisition" else "audit_deepeval_bytes",
            fail,
        )
        store = tmp_path / exception.__name__
        if exception is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                command.run_record_deepeval(path, store, "sample")
            assert not store.exists()
        else:
            report = command.run_record_deepeval(path, store, "sample")
            assert report["exit_code"] == ANALYSIS_ERROR_EXIT
            analysis = cast(dict[str, Any], report["analysis"])
            assert analysis["phase"] == phase
            assert analysis["error"]["code"] == "INTERNAL_ERROR"
            assert "private source text" not in canonical_bytes(report).decode()


def test_identity_mismatch_refuses_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    original = command.audit_deepeval_bytes

    def audit(raw: bytes) -> dict[str, object]:
        return {**original(raw), "raw_hash": "sha256:" + "0" * 64}

    monkeypatch.setattr(command, "audit_deepeval_bytes", audit)
    with pytest.raises(command.EvidenceError) as caught:
        command.run_record_deepeval(path, store, "sample")
    assert caught.value.payload["code"] == "INTERNAL_ERROR"
    assert not store.exists()


def test_human_output_has_interpretation_limits_not_source_terminal_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    payload = json.loads(path.read_bytes())
    payload["testCases"][0]["name"] = "\x1b[31mprivate text"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert command.main([str(path), "--root", str(store), "--id", "sample"]) == 0
    streams = capsys.readouterr()
    assert streams.err == "" and "\x1b" not in streams.out and "private text" not in streams.out
    assert "Planned evaluations unknown; source claims unverified" in streams.out
