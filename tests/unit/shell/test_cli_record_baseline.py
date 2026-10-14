"""One-call baseline record retains one captured exchange and bounded diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _baseline_exchange import build_baseline_exchange
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import record_baseline
from underwrite.cli.main import main
from underwrite.instrument.evidence.goppi_baseline import FILES
from underwrite.instrument.ingest import goppi_baseline
from underwrite.instrument.ingest.local_capture import RootReader
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service import records

PRIVATE = "PRIVATE_BASELINE_ANSWER"
FILE_COUNT = 7
DIAGNOSTIC_EXIT = 2
STORAGE_EXIT = 3


def _exchange(directory: Path, *, private: bool = False) -> dict[str, bytes]:
    exchange = build_baseline_exchange()
    if private:
        exchange.export["include_answers"] = True
        for row in exchange.rows:
            row["answer"] = {
                "included": True,
                "availability": "included_private",
                "value": PRIVATE,
            }
            row["outcome"]["reason"] = PRIVATE
    files = exchange.files()
    directory.mkdir()
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return files


def _invoke(
    exchange: Path, store: Path, capsys: pytest.CaptureFixture[str], identifier: str = "baseline"
) -> tuple[int, dict[str, Any], str, str]:
    code = main(
        [
            "records",
            "audit-baseline",
            "--exchange",
            str(exchange),
            "--root",
            str(store),
            "--id",
            identifier,
            "--json",
        ]
    )
    streams = capsys.readouterr()
    return code, json.loads(streams.out or streams.err), streams.out, streams.err


def test_exact_five_bytes_result_and_readback_after_exchange_removal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    files = _exchange(exchange, private=True)
    code, report, stdout, stderr = _invoke(exchange, store, capsys)
    assert code == report["exit_code"] == 0 and stderr == ""
    assert PRIVATE not in stdout and str(exchange) not in stdout and str(store) not in stdout
    packaged_validator("baseline_record.v1").validate(report)  # pyright: ignore[reportUnknownMemberType]
    assert report["source"]["files"] == [
        {"name": name, "raw_hash": digest_bytes(files[name]), "bytes": len(files[name])}
        for name in FILES
    ]
    result = report["analysis"]["report"]
    assert result["source"]["identity"]["members"] == {
        name: digest_bytes(files[name]) for name in FILES
    }
    assert report["storage"]["verification"]["files_checked"] == FILE_COUNT
    for name in FILES:
        assert records.read_record_file(store, "baseline", "source/" + name).data == files[name]
    invocation = json.loads(records.read_record_file(store, "baseline", "invocation.json").data)
    assert invocation == {
        "command": "records audit-baseline",
        "profile": "goppi-baseline-exchange-v2",
        "execution_mode": "in_process",
        "record_id": "baseline",
        "analysis_state": "result",
        "analysis_exit_code": 0,
    }
    for name in FILES:
        (exchange / name).unlink()
    assert main(["records", "verify", "--root", str(store), "--id", "baseline", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["files_checked"] == FILE_COUNT
    assert main(["records", "show", "--root", str(store), "--id", "baseline", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["record"]["kind"] == "goppi.baseline-exchange"
    assert main(
        [
            "records", "read-json", "--root", str(store), "--id", "baseline",
            "--name", "analysis/evidence_result.json", "--json",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["content"] == result


def test_complete_invalid_input_saves_error_and_collision_preserves_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    files = _exchange(exchange)
    files["export.json"] = b"{invalid"
    (exchange / "export.json").write_bytes(files["export.json"])
    code, report, _, stderr = _invoke(exchange, store, capsys)
    assert code == report["exit_code"] == DIAGNOSTIC_EXIT and stderr == ""
    assert report["analysis"]["error"]["code"] == "INVALID_INPUT"
    assert report["storage"]["verification"]["files_checked"] == FILE_COUNT
    assert records.read_record_file(store, "baseline", "source/export.json").data == b"{invalid"
    saved = records.read_record_file(store, "baseline", "analysis/error.json").data
    assert saved == canonical_bytes(report["analysis"]["error"]) + b"\n"
    manifest = records.read_record(store, "baseline").manifest
    manifest_files = cast(list[dict[str, Any]], manifest["files"])
    assert "analysis/evidence_result.json" not in [
        item["name"] for item in manifest_files
    ]
    collision, after, _, _ = _invoke(exchange, store, capsys)
    assert collision == after["exit_code"] == STORAGE_EXIT
    assert after["storage"]["error"]["code"] == "RECORD_EXISTS"
    assert records.read_record_file(store, "baseline", "analysis/error.json").data == saved


def test_incomplete_capture_refuses_without_consuming_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    files = _exchange(exchange)
    (exchange / "scope.json").unlink()
    code, error, stdout, stderr = _invoke(exchange, store, capsys)
    assert code == DIAGNOSTIC_EXIT and stdout == "" and stderr
    assert error["schema"] == "evidence_error.v1"
    assert not store.exists()
    (exchange / "scope.json").write_bytes(files["scope.json"])
    assert _invoke(exchange, store, capsys)[0] == 0


def test_capture_once_and_later_disk_mutation_does_not_change_stored_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    files = _exchange(exchange)
    original = goppi_baseline.RootReader.capture_relative
    seen: list[str] = []

    def mutate(self: RootReader, name: str, **kwargs: Any) -> Any:
        seen.append(name)
        result = original(self, name, **kwargs)
        (exchange / name).write_bytes(b"later mutation")
        return result

    monkeypatch.setattr(goppi_baseline.RootReader, "capture_relative", mutate)
    code, report, _, _ = _invoke(exchange, store, capsys)
    assert code == 0 and seen == list(FILES)
    assert report["analysis"]["report"]["source"]["identity"]["members"] == {
        name: digest_bytes(files[name]) for name in FILES
    }
    for name in FILES:
        assert records.read_record_file(store, "baseline", "source/" + name).data == files[name]


def test_schema_fault_refuses_before_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    _exchange(exchange)
    original = record_baseline.packaged_validator

    def refuse(name: str) -> Any:
        if name == "baseline_record.v1":
            raise SchemaConfigurationError("fault")
        return original(name)

    monkeypatch.setattr(record_baseline, "packaged_validator", refuse)
    code, error, stdout, _ = _invoke(exchange, store, capsys)
    assert code == DIAGNOSTIC_EXIT and stdout == ""
    assert error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert not store.exists()


def test_analysis_size_refuses_before_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    _exchange(exchange)

    def oversized(value: object) -> bytes:
        del value
        return b"x" * (records.RECORD_READ_MAX_BYTES + 1)

    monkeypatch.setattr(record_baseline, "canonical_bytes", oversized)
    code, error, stdout, _ = _invoke(exchange, store, capsys)
    assert code == DIAGNOSTIC_EXIT and stdout == ""
    assert (error["schema"], error["code"]) == (
        "evidence_error.v1", "OUTPUT_LIMIT_EXCEEDED"
    )
    assert not store.exists()


def test_schema_evaluation_fault_after_capture_refuses_before_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    _exchange(exchange)
    original = record_baseline.packaged_validator

    class BrokenValidator:
        def validate(self, value: object) -> None:
            del value
            raise RuntimeError(PRIVATE)

    def validator(name: str) -> Any:
        return BrokenValidator() if name == "evidence_result.v1" else original(name)

    monkeypatch.setattr(record_baseline, "packaged_validator", validator)
    code, error, stdout, stderr = _invoke(exchange, store, capsys)
    assert code == DIAGNOSTIC_EXIT and stdout == "" and PRIVATE not in stderr
    assert (error["schema"], error["code"]) == (
        "evidence_error.v1", "SCHEMA_CONFIGURATION_ERROR"
    )
    assert not store.exists()


def test_binding_mismatch_refuses_before_storage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    _exchange(exchange)
    original = record_baseline.project_baseline_result

    def unbind(documents: Any) -> dict[str, object]:
        result = original(documents)
        source = cast(dict[str, Any], result["source"])
        source["identity"]["members"]["rows.jsonl"] = "sha256:" + "0" * 64
        return result

    monkeypatch.setattr(record_baseline, "project_baseline_result", unbind)
    code, error, stdout, _ = _invoke(exchange, store, capsys)
    assert code == DIAGNOSTIC_EXIT and stdout == "" and error["code"] == "INPUT_BINDING_MISMATCH"
    assert not store.exists()


def test_post_publication_failure_returns_known_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    _exchange(exchange)
    original = records.verify_record

    def fail(root: Path, identifier: str) -> dict[str, object]:
        del root, identifier
        raise OSError("private text")

    from underwrite.cli import record_capture

    monkeypatch.setattr(record_capture, "verify_record", fail)
    code, report, stdout, _ = _invoke(exchange, store, capsys)
    assert code == report["exit_code"] == STORAGE_EXIT
    assert "private text" not in stdout
    assert report["storage"]["published_record"] == {
        "record_id": "baseline",
        "manifest_digest": records.read_record(store, "baseline").digest,
    }
    monkeypatch.setattr(record_capture, "verify_record", original)


@pytest.mark.parametrize("publish_first", [False, True])
def test_storage_failure_with_unknown_identity_requires_inspection_before_retry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    publish_first: bool,
) -> None:
    exchange, store = tmp_path / "exchange", tmp_path / "store"
    _exchange(exchange)
    from underwrite.cli import record_capture

    original = record_capture.save_captured_record

    def fail(root: Path, spec: records.CapturedRecordSpec) -> records.RecordSnapshot:
        if publish_first:
            original(root, spec)
        raise OSError("private storage text")

    monkeypatch.setattr(record_capture, "save_captured_record", fail)
    code, report, stdout, stderr = _invoke(exchange, store, capsys)
    assert code == report["exit_code"] == STORAGE_EXIT and stderr == ""
    assert "private storage text" not in stdout
    assert report["storage"]["published_record"] is None
    assert store.exists() is publish_first
    if publish_first:
        assert records.verify_record(store, "baseline")["valid"] is True
