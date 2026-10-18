"""One-call private retention for the pinned Langfuse CLI file profile."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import main
from underwrite.cli import record_capture as storage
from underwrite.cli import record_langfuse as command
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES
from underwrite.instrument.ingest.langfuse_cli import CLI_PROFILE, SOURCE_FORMATS, SOURCE_VERSION
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service import records
from underwrite.service.records import read_record, read_record_file, verify_record

ROOT = repo_root(Path(__file__).resolve())
FIXTURES = ROOT / "fixtures/examples/langfuse-cli"
ERROR_EXIT = 2
STORAGE_EXIT = 3


def _inputs(tmp_path: Path, *, body: bool = False) -> dict[str, Path]:
    names = {
        "observations": "observations-body.json" if body else "observations.json",
        "scores": "scores-body.json" if body else "scores.json",
    }
    paths = {role: tmp_path / name for role, name in names.items()}
    for role, path in paths.items():
        path.write_bytes((FIXTURES / names[role]).read_bytes())
    return paths


def _argv(paths: dict[str, Path], store: Path, *, identifier: str = "sample") -> list[str]:
    return [
        "records",
        "audit-langfuse",
        "--observations",
        str(paths["observations"]),
        "--scores",
        str(paths["scores"]),
        "--profile",
        "cli-1.2.4",
        "--root",
        str(store),
        "--id",
        identifier,
        "--json",
    ]


def _run(paths: dict[str, Path], store: Path, identifier: str = "sample") -> dict[str, Any]:
    return cast(
        dict[str, Any],
        command.run_record_langfuse(
            paths["observations"], paths["scores"], store, identifier, profile=CLI_PROFILE
        ),
    )


def test_cli_profile_saves_exact_captured_bytes_and_recovers_after_source_removal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    originals = {role: path.read_bytes() for role, path in paths.items()}
    assert main.main(_argv(paths, store)) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    result: dict[str, Any] = json.loads(streams.out)
    assert result["schema"] == "langfuse_cli_record.v1"
    assert result["analysis"]["state"] == "result"
    assert result["storage"]["state"] == "saved"
    assert result["analysis"]["report"]["schema"] == "langfuse_cli_accounting.v1"
    manifest = cast(dict[str, Any], read_record(store, "sample").manifest)
    assert manifest["kind"] == "langfuse.cli-accounting"
    assert manifest["metadata"]["profile"] == "cli-1.2.4"
    stored_files = {item["name"]: item for item in manifest["files"]}
    for role, raw in originals.items():
        expected = digest_bytes(raw)
        assert result["sources"][role]["raw_hash"] == expected
        assert result["analysis"]["report"]["sources"][role]["raw_hash"] == expected
        assert stored_files[f"source/{role}.bin"]["sha256"] == expected
        assert manifest["metadata"][f"{role}_format"] == SOURCE_FORMATS[role]
        assert manifest["metadata"][f"{role}_format_version"] == SOURCE_VERSION
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["profile"] == "cli-1.2.4"
    assert set(invocation["source_selectors"]) == set(originals)
    for path in paths.values():
        path.unlink()
    assert main.main(["records", "verify", "--root", str(store), "--id", "sample", "--json"]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["valid"] is True
    assert (
        main.main(
            [
                "records",
                "read-json",
                "--root",
                str(store),
                "--id",
                "sample",
                "--name",
                "analysis/accounting.json",
                "--json",
            ]
        )
        == 0
    )
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["content"] == result["analysis"]["report"]
    for role, raw in originals.items():
        selected = read_record_file(
            store, "sample", f"source/{role}.bin", max_bytes=DEFAULT_MAX_BYTES
        )
        assert selected.data == raw


def test_body_capture_keeps_typed_scores_and_reported_pagination(tmp_path: Path) -> None:
    paths, store = _inputs(tmp_path, body=True), tmp_path / "store"
    result = _run(paths, store)
    assert result["exit_code"] == 0
    report = result["analysis"]["report"]
    assert report["retrieval"]["observations"]["capture_kind"] == "output_body"
    assert report["retrieval"]["scores"]["http_status"] is None
    assert report["scores"][1]["value"] is False
    assert report["scores"][3]["value"] == "synthetic review"
    assert report["scores"][4]["data_type"] == "CORRECTION"
    assert report["retrieval"]["scores"]["pagination"]["truncated"] is False


@pytest.mark.parametrize("missing", [("observations",), ("scores",), ("observations", "scores")])
def test_cli_profile_attempts_both_roles_and_saves_only_complete_captures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: tuple[str, ...]
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    for role in missing:
        paths[role].unlink()
    actual_read = command.read_langfuse_file
    attempts: list[str] = []

    def read(path: Path, role: str) -> bytes:
        attempts.append(role)
        return actual_read(path, role)

    monkeypatch.setattr(command, "read_langfuse_file", read)
    result = _run(paths, store)
    assert attempts == ["observations", "scores"]
    assert result["exit_code"] == ERROR_EXIT
    assert result["analysis"]["phase"] == "acquisition"
    assert result["analysis"]["error"] == result["sources"][missing[0]]["error"]
    assert result["storage"]["state"] == "saved"
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["profile"] == "cli-1.2.4"
    for role in ("observations", "scores"):
        if role in missing:
            assert result["sources"][role]["state"] == "unavailable"
            assert not (store / "records/sample/files" / f"source/{role}.bin").exists()
        else:
            assert result["sources"][role]["state"] == "captured"


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("malformed", "INVALID_JSON_INPUT"),
        ("non_nfc", "LANGFUSE_CLI_NONCANONICAL_SCORE_VALUE"),
    ],
)
def test_complete_invalid_cli_score_saves_finite_diagnostic_and_raw_bytes(
    tmp_path: Path, kind: str, reason: str
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    if kind == "malformed":
        raw = b"\xffprivate score data"
    else:
        document = json.loads(paths["scores"].read_bytes())
        document["body"]["data"][3]["value"] = "private-e\u0301"
        raw = json.dumps(document, ensure_ascii=False).encode()
    paths["scores"].write_bytes(raw)
    result = _run(paths, store)
    assert result["exit_code"] == ERROR_EXIT
    assert result["analysis"]["state"] == "error"
    assert result["analysis"]["phase"] == "analysis"
    assert result["analysis"]["error"]["reason"] == reason
    assert result["storage"]["state"] == "saved"
    assert (
        read_record_file(store, "sample", "source/scores.bin", max_bytes=DEFAULT_MAX_BYTES).data
        == raw
    )
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["profile"] == "cli-1.2.4"
    assert "private" not in canonical_bytes(result).decode()


def test_unknown_profile_and_missing_contract_refuse_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(path: Path, role: str) -> bytes:
        pytest.fail("source read occurred before selection/contracts")

    monkeypatch.setattr(command, "read_langfuse_file", forbidden)
    with pytest.raises(command.EvidenceError) as unknown:
        command.run_record_langfuse(
            tmp_path / "obs",
            tmp_path / "scores",
            tmp_path / "store",
            "sample",
            profile="unsupported",
        )
    assert unknown.value.payload["code"] == "UNSUPPORTED_FORMAT"

    def missing(name: str) -> Any:
        if name == command.CLI_RECORD_SCHEMA:
            raise SchemaConfigurationError("private schema path")
        return original(name)

    original = command.packaged_validator
    monkeypatch.setattr(command, "packaged_validator", missing)
    with pytest.raises(SchemaConfigurationError):
        command.run_record_langfuse(
            tmp_path / "obs",
            tmp_path / "scores",
            tmp_path / "store",
            "sample",
            profile="cli-1.2.4",
        )
    assert not (tmp_path / "store").exists()


def test_cli_profile_reuse_of_id_reports_storage_failure_without_erasing_analysis(
    tmp_path: Path,
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    for attempt in range(2):
        result = _run(paths, store)
        assert result["analysis"]["state"] == "result"
        if attempt:
            assert result["exit_code"] == STORAGE_EXIT
            assert result["storage"]["error"]["code"] == "RECORD_EXISTS"
            assert result["storage"]["published_record"] is None
        else:
            assert result["exit_code"] == 0


def test_cli_storage_verification_failure_preserves_analysis_and_known_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"

    def fail(root: Path, identifier: str) -> dict[str, object]:
        del root, identifier
        raise records.RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(storage, "verify_record", fail)
    result = _run(paths, store)
    assert result["analysis"]["state"] == "result"
    assert result["exit_code"] == STORAGE_EXIT
    assert result["storage"]["error"]["code"] == "STORE_IO_ERROR"
    assert result["storage"]["published_record"]["record_id"] == "sample"


def test_cli_tampered_copy_fails_whole_verify_and_selected_source_read(tmp_path: Path) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    assert _run(paths, store)["exit_code"] == 0
    copied = tmp_path / "copied-store"
    shutil.copytree(store, copied)
    selected = copied / "records/sample/files/source/scores.bin"
    raw = selected.read_bytes()
    selected.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    whole = verify_record(copied, "sample")
    assert whole["valid"] is False
    findings = cast(list[dict[str, Any]], whole["findings"])
    assert {item["code"] for item in findings} == {"FILE_DIGEST_MISMATCH"}
    with pytest.raises(records.RecordError) as caught:
        read_record_file(copied, "sample", "source/scores.bin", max_bytes=DEFAULT_MAX_BYTES)
    assert caught.value.code == "RECORD_FILE_INTEGRITY_MISMATCH"


def test_cli_output_admission_precedes_store_and_interrupts_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _inputs(tmp_path)
    store = tmp_path / "store"
    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", 1)
    with pytest.raises(command.EvidenceError) as limited:
        _run(paths, store)
    assert limited.value.payload["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert not store.exists()
    monkeypatch.undo()
    interrupted = KeyboardInterrupt("synthetic interruption")

    def stop(path: Path, role: str) -> bytes:
        raise interrupted

    monkeypatch.setattr(command, "read_langfuse_file", stop)
    with pytest.raises(KeyboardInterrupt) as caught:
        _run(paths, store)
    assert caught.value is interrupted
    assert not store.exists()
