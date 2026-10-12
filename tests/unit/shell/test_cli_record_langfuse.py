"""Same-buffer Langfuse accounting and independently reported storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import record_capture as storage
from underwrite.cli import record_langfuse as command
from underwrite.cli import records as record_cli
from underwrite.instrument.ingest import langfuse_accounting as adapter
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service import records

ANALYSIS_ERROR_EXIT = 2
STORAGE_ERROR_EXIT = 3

GOLDEN = Path(__file__).resolve().parents[3] / "fixtures/golden/external/langfuse"


def inputs(tmp_path: Path) -> dict[str, Path]:
    names = {
        "observations": "observations_v2-2026-09-14T06-40-00.json",
        "scores": "scores-2026-09-14T06-40-00.json",
    }
    paths = {role: tmp_path / (role + ".json") for role in names}
    for role, path in paths.items():
        path.write_bytes((GOLDEN / names[role]).read_bytes())
    return paths


def run(paths: dict[str, Path], store: Path, *, as_json: bool = True) -> dict[str, Any]:
    return command.run_record_langfuse(
        paths["observations"], paths["scores"], store, "sample", as_json=as_json
    )


def files(store: Path) -> Path:
    return store / "records/sample/files"


def test_same_buffer_capture_survives_original_mutation_and_independent_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    originals = {role: path.read_bytes() for role, path in paths.items()}
    expected = adapter.audit_langfuse_files(paths["observations"], paths["scores"])
    real_read = command.read_langfuse_file
    reads: list[str] = []

    def read(path: Path, role: str) -> bytes:
        reads.append(role)
        raw = real_read(path, role)
        path.unlink()
        return raw

    monkeypatch.setattr(command, "read_langfuse_file", read)
    result = run(paths, store)
    assert result["exit_code"] == 0 and reads == list(paths)
    assert result["analysis"]["report"] == expected
    assert result["storage"]["verification"]["files_checked"] == len(originals) + 2
    for role, raw in originals.items():
        assert result["sources"][role] == {
            "state": "captured",
            "bytes": len(raw),
            "raw_hash": digest_bytes(raw),
        }
        assert (files(store) / f"source/{role}.bin").read_bytes() == raw
    assert (
        json.loads(records.read_record_file(store, "sample", "analysis/accounting.json").data)
        == expected
    )
    invocation = json.loads((files(store) / "invocation.json").read_bytes())
    assert invocation["sources"] == result["sources"]
    assert invocation["execution_mode"] == "in_process"
    assert invocation["analysis_exit_code_scope"] == "component_contract_not_os_process"
    assert str(tmp_path) not in canonical_bytes(result).decode()


@pytest.mark.parametrize(
    "missing", [(), ("observations",), ("scores",), ("observations", "scores")]
)
def test_each_acquisition_attempted_and_all_diagnostics_retained(
    tmp_path: Path,
    missing: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    for role in missing:
        paths[role].unlink()
    reads: list[str] = []
    original = command.read_langfuse_file

    def read(path: Path, role: str) -> bytes:
        reads.append(role)
        return original(path, role)

    monkeypatch.setattr(command, "read_langfuse_file", read)
    result = run(paths, store)
    assert reads == list(paths)
    assert result["exit_code"] == (2 if missing else 0)
    assert result["storage"]["verification"]["files_checked"] == len(paths) - len(missing) + 2
    invocation = json.loads((files(store) / "invocation.json").read_bytes())
    assert invocation["sources"] == result["sources"]
    if missing:
        assert result["analysis"]["phase"] == "acquisition"
        assert result["analysis"]["error"] == result["sources"][missing[0]]["error"]
    for role in missing:
        assert result["sources"][role]["error"]["location"] == "/" + role
        assert not (files(store) / f"source/{role}.bin").exists()


@pytest.mark.parametrize("raw", [b"not json", b"\xff", b"{}"])
def test_complete_invalid_source_preserved(tmp_path: Path, raw: bytes) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    paths["observations"].write_bytes(raw)
    result = run(paths, store)
    assert result["exit_code"] == ANALYSIS_ERROR_EXIT
    assert result["analysis"]["phase"] == "analysis"
    assert result["storage"]["verification"]["files_checked"] == len(paths) + 2
    assert (files(store) / "source/observations.bin").read_bytes() == raw
    assert (
        json.loads((files(store) / "analysis/error.json").read_bytes())
        == result["analysis"]["error"]
    )


def test_input_limit_has_no_partial_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    monkeypatch.setattr(adapter, "DEFAULT_MAX_BYTES", 1)
    result = run(paths, store)
    assert result["exit_code"] == ANALYSIS_ERROR_EXIT
    assert result["analysis"]["error"]["code"] == "INPUT_LIMIT_EXCEEDED"
    assert not (files(store) / "source").exists()


@pytest.mark.parametrize("fault", ["configuration", "identifier"])
def test_preflight_before_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    def read(path: Path, role: str) -> bytes:
        del path, role
        pytest.fail("read must not occur")

    def fail(name: str) -> Any:
        del name
        raise SchemaConfigurationError("INVALID_OBSERVATION_SCHEMA")

    monkeypatch.setattr(command, "read_langfuse_file", read)
    if fault == "configuration":
        monkeypatch.setattr(command, "packaged_validator", fail)
    with pytest.raises((SchemaConfigurationError, command.EvidenceError)):
        command.run_record_langfuse(
            tmp_path / "obs",
            tmp_path / "scores",
            tmp_path / "store",
            "../bad" if fault == "identifier" else "sample",
        )
    assert not (tmp_path / "store").exists()


@pytest.mark.parametrize("delta", [0, -1])
def test_saved_analysis_byte_limit_includes_newline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    delta: int,
) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    expected = adapter.audit_langfuse_files(paths["observations"], paths["scores"])
    maximum = len(canonical_bytes(expected)) + 1
    monkeypatch.setattr(command, "RECORD_READ_MAX_BYTES", maximum + delta)
    if delta:
        with pytest.raises(command.EvidenceError) as caught:
            run(paths, store)
        assert caught.value.payload["code"] == "OUTPUT_LIMIT_EXCEEDED"
        assert not store.exists()
    else:
        assert run(paths, store)["exit_code"] == 0
        assert len((files(store) / "analysis/accounting.json").read_bytes()) == maximum


@pytest.mark.parametrize("as_json", [True, False])
def test_aggregate_admission_all_finite_storage_errors_and_newline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    as_json: bool,
) -> None:
    paths = inputs(tmp_path)
    report = run(paths, tmp_path / "reference")
    identity = {"record_id": "sample", "manifest_digest": report["storage"]["manifest_digest"]}
    variants = [report]
    for error in record_cli.record_error_candidates():
        for known in (None, identity):
            variants.append(
                {
                    **report,
                    "exit_code": 3,
                    "storage": {"state": "error", "error": error, "published_record": known},
                }
            )
    maximum = max(
        len(canonical_bytes(item) if as_json else command.render_human(item).encode()) + 1
        for item in variants
    )
    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", maximum)
    assert run(paths, tmp_path / "accepted", as_json=as_json)["exit_code"] == 0
    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", maximum - 1)
    with pytest.raises(command.EvidenceError):
        run(paths, tmp_path / "refused", as_json=as_json)
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize("failure", ["known", "unexpected", "conflict"])
def test_storage_errors_preserve_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    if failure == "conflict":
        run(paths, store)
    elif failure == "known":

        def verify(root: Path, identifier: str) -> dict[str, object]:
            del root, identifier
            raise records.RecordError("STORE_IO_ERROR")

        monkeypatch.setattr(storage, "verify_record", verify)
    else:

        def save(root: Path, spec: records.CapturedRecordSpec) -> records.RecordSnapshot:
            del root, spec
            raise RuntimeError("private source text")

        monkeypatch.setattr(storage, "save_captured_record", save)
    result = run(paths, store)
    assert result["exit_code"] == STORAGE_ERROR_EXIT and result["analysis"]["state"] == "result"
    assert (
        result["storage"]["error"]["code"]
        == {"known": "STORE_IO_ERROR", "unexpected": "INTERNAL_ERROR", "conflict": "RECORD_EXISTS"}[
            failure
        ]
    )
    assert (result["storage"]["published_record"] is not None) == (failure == "known")
    assert "private source text" not in canonical_bytes(result).decode()


def test_cli_json_and_human(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = inputs(tmp_path)
    for as_json in (True, False):
        args = [
            "--observations",
            str(paths["observations"]),
            "--scores",
            str(paths["scores"]),
            "--root",
            str(tmp_path / str(as_json)),
            "--id",
            "sample",
        ]
        assert command.main(args + (["--json"] if as_json else [])) == 0
        streams = capsys.readouterr()
        assert streams.err == "" and str(tmp_path) not in streams.out
        if as_json:
            report = json.loads(streams.out)
            assert streams.out.encode() == canonical_bytes(report) + b"\n"
        else:
            assert "Planned evaluations and export completeness unknown" in streams.out


@pytest.mark.parametrize("phase", ["acquisition", "analysis"])
def test_unexpected_failure_has_finite_private_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    if phase == "acquisition":

        def read(path: Path, role: str) -> bytes:
            del path, role
            raise RuntimeError("secret producer text")

        monkeypatch.setattr(command, "read_langfuse_file", read)
    else:

        def audit(observations: bytes, scores: bytes) -> dict[str, object]:
            del observations, scores
            raise RuntimeError("secret producer text")

        monkeypatch.setattr(command, "audit_langfuse_bytes", audit)
    report = run(paths, store)
    assert report["exit_code"] == ANALYSIS_ERROR_EXIT
    assert report["analysis"]["phase"] == phase
    assert report["analysis"]["error"]["code"] == "INTERNAL_ERROR"
    assert "secret producer text" not in canonical_bytes(report).decode()


def test_source_identity_mismatch_refused_before_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    original = command.audit_langfuse_bytes

    def audit(observations: bytes, scores: bytes) -> dict[str, object]:
        report = original(observations, scores)
        identities = cast(dict[str, dict[str, object]], report["sources"])
        identities["scores"]["raw_hash"] = "sha256:" + "0" * 64
        return report

    monkeypatch.setattr(command, "audit_langfuse_bytes", audit)
    with pytest.raises(command.EvidenceError) as caught:
        run(paths, store)
    assert caught.value.payload["code"] == "INTERNAL_ERROR"
    assert not store.exists()


def test_undecodable_display_path_keeps_analysis_and_refuses_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    originals = {role: path.read_bytes() for role, path in paths.items()}

    def read(path: Path, role: str) -> bytes:
        del path
        return originals[role]

    monkeypatch.setattr(command, "read_langfuse_file", read)
    paths["observations"] = Path("private-\ud800")
    report = run(paths, store)
    assert report["exit_code"] == STORAGE_ERROR_EXIT
    assert report["analysis"]["state"] == "result"
    assert report["storage"]["error"]["code"] == "INVALID_SPEC"
    assert not store.exists()


def test_record_file_admission_failure_keeps_analysis_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, store = inputs(tmp_path), tmp_path / "store"
    monkeypatch.setattr(records, "MAX_FILE_BYTES", 1)
    report = run(paths, store)
    assert report["exit_code"] == STORAGE_ERROR_EXIT
    assert report["analysis"]["state"] == "result"
    assert report["storage"]["error"]["code"] == "INPUT_LIMIT_EXCEEDED"
    assert not store.exists()
