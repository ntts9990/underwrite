"""Fixed in-process accounting capture preserves independent analysis/storage outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import record_capture
from underwrite.cli import record_promptfoo as command
from underwrite.cli import records as record_cli
from underwrite.instrument.ingest import accounting as adapter
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service import records

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "fixtures/golden/external/promptfoo/results.json"
ANALYSIS_ERROR_EXIT = 2
STORAGE_ERROR_EXIT = 3


def invoke(
    path: Path, store: Path, capsys: pytest.CaptureFixture[str], identifier: str = "sample"
) -> tuple[int, dict[str, Any], str, str]:
    code = record_cli.main(
        [
            "audit-promptfoo",
            str(path),
            "--root",
            str(store),
            "--id",
            identifier,
            "--json",
        ]
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
    assert report["analysis"]["report"] == adapter.audit_promptfoo_file(path)
    assert report["source"] == {
        "state": "captured",
        "raw_hash": digest_bytes(raw),
        "bytes": len(raw),
    }
    assert report["storage"]["state"] == "saved"
    assert report["storage"]["verification"]["valid"] is True
    files = stored_files(store)
    assert (files / "source/promptfoo.bin").read_bytes() == raw
    assert (
        json.loads((files / "analysis/accounting.json").read_bytes())
        == report["analysis"]["report"]
    )
    invocation = json.loads((files / "invocation.json").read_bytes())
    assert invocation["command"] == "records audit-promptfoo"
    assert invocation["execution_mode"] == "in_process"
    assert invocation["analysis_exit_code"] == 0
    assert invocation["analysis_exit_code_scope"] == "component_contract_not_os_process"
    assert invocation["aggregate_outcome_location"] == "command_output"
    assert invocation["arguments"] == {
        "source_path_display": str(path),
        "root_display": str(store),
        "record_id": "sample",
        "selection_mode": "auto",
        "reader_profile": report["analysis"]["report"]["reader_profile"],
        "producer_version_state": "declared",
        "producer_version": "0.123.0",
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
    assert (files / "source/promptfoo.bin").read_bytes() == raw
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
    original = command.read_promptfoo_file
    reads: list[Path] = []

    def mutate(selected: Path) -> bytes:
        reads.append(selected)
        raw = original(selected)
        selected.write_bytes(b"changed after the capture")
        return raw

    monkeypatch.setattr(command, "read_promptfoo_file", mutate)
    report = command.run_record_promptfoo(path, store, "sample")
    assert report["exit_code"] == 0 and reads == [path]
    analysis = cast(dict[str, Any], report["analysis"])
    assert analysis["report"]["raw_hash"] == digest_bytes(original_bytes)
    assert (stored_files(store) / "source/promptfoo.bin").read_bytes() == original_bytes


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
    args = [
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        *(["--json"] if as_json else []),
    ]
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
    monkeypatch.setattr(command, "read_promptfoo_file", forbidden)
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

    monkeypatch.setattr(command, "read_promptfoo_file", forbidden)
    with pytest.raises(command.EvidenceError) as caught:
        command.run_record_promptfoo(tmp_path / "missing", tmp_path / "store", "../invalid")
    assert caught.value.payload["code"] == "USAGE_ERROR"


def test_undecodable_display_path_is_storage_failure_not_lost_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = GOLDEN.read_bytes()

    def read(path: Path) -> bytes:
        del path
        return raw

    monkeypatch.setattr(command, "read_promptfoo_file", read)
    store = tmp_path / "store"
    report = command.run_record_promptfoo(Path("private-\ud800"), store, "sample")
    assert report["exit_code"] == STORAGE_ERROR_EXIT
    assert cast(dict[str, object], report["analysis"])["state"] == "result"
    assert cast(dict[str, Any], report["storage"])["error"]["code"] == "INVALID_SPEC"
    assert not store.exists()


def test_bytes_entry_point_keeps_own_input_cap_and_matches_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source(tmp_path)
    raw = path.read_bytes()
    assert adapter.audit_promptfoo_bytes(raw) == (adapter.audit_promptfoo_file(path))
    monkeypatch.setattr(adapter, "DEFAULT_MAX_BYTES", len(raw) - 1)
    with pytest.raises(adapter.AccountingCaseError) as caught:
        adapter.audit_promptfoo_bytes(raw)
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


def test_unpublished_reservation_is_not_reused_or_deleted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    command.run_record_promptfoo(path, store, "first")
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
    report = command.run_record_promptfoo(path, tmp_path / "reference", "sample")
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
    actual = command.run_record_promptfoo(path, tmp_path / "accepted", "sample", as_json=as_json)
    assert actual["exit_code"] == 0
    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", maximum - 1)
    with pytest.raises(command.EvidenceError) as caught:
        command.run_record_promptfoo(path, tmp_path / "refused", "sample", as_json=as_json)
    assert caught.value.payload["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize("all_candidates", [False, True])
def test_large_analysis_full_scans_do_not_scale_with_error_candidate_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    all_candidates: bool,
) -> None:
    from underwrite.cli import evidence
    from underwrite.instrument.evidence.accounting import ROW_LIMIT, audit_promptfoo

    path = source(tmp_path)
    base = adapter.audit_promptfoo_file(path)
    large = {
        **base,
        **audit_promptfoo(
            {"successes": ROW_LIMIT, "failures": 0, "errors": 0},
            [{"success": True, "failureReason": 1, "gradingResult": {"pass": False}}] * ROW_LIMIT,
        ),
    }
    assert len(cast(list[object], large["findings"])) == ROW_LIMIT

    def analyze(raw: bytes) -> dict[str, object]:
        del raw
        return large

    monkeypatch.setattr(command, "audit_promptfoo_bytes", analyze)
    candidates = record_cli.record_error_candidates()

    def error_variants() -> tuple[dict[str, object], ...]:
        return candidates if all_candidates else candidates[:1]

    monkeypatch.setattr(record_capture, "record_error_candidates", error_variants)
    serialized: list[bool] = []
    validated: list[bool] = []
    real_canonical = record_capture.canonical_bytes
    real_check = record_capture.validate
    real_validate = evidence.validate

    def is_aggregate(value: object) -> bool:
        return (
            isinstance(value, dict)
            and cast(dict[str, object], value).get("schema") == command.AUTO_RECORD_SCHEMA
        )

    def encode(value: object) -> bytes:
        if is_aggregate(value):
            serialized.append(True)
        return real_canonical(value)

    def check(validator: command.Validator, value: object, *args: Any) -> None:
        if is_aggregate(value):
            validated.append(True)
        real_check(validator, value, *args)

    def validate_final(*args: Any, **kwargs: Any) -> None:
        if is_aggregate(args[1]):
            validated.append(True)
        real_validate(*args, **kwargs)

    monkeypatch.setattr(record_capture, "canonical_bytes", encode)
    monkeypatch.setattr(evidence, "canonical_bytes", encode)
    monkeypatch.setattr(record_capture, "validate", check)
    monkeypatch.setattr(evidence, "validate", validate_final)
    code, report, _, _ = invoke(path, tmp_path / "store", capsys)
    assert code == 0 and report["storage"]["state"] == "saved"
    expected_passes = len(("admission", "final_emitter"))
    assert len(serialized) == len(validated) == expected_passes


@pytest.mark.parametrize("during_verification", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_shared_capture_limits_unexpected_errors_without_swallowing_interrupts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    during_verification: bool,
    failure: type[BaseException],
) -> None:
    path, store = source(tmp_path), tmp_path / "store"
    problem = failure("private diagnostic")

    def fail(*args: object) -> Any:
        del args
        raise problem

    monkeypatch.setattr(
        record_capture, "verify_record" if during_verification else "save_captured_record", fail
    )
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt) as caught:
            command.run_record_promptfoo(path, store, "sample")
        assert caught.value is problem
        return
    report = command.run_record_promptfoo(path, store, "sample")
    storage = cast(dict[str, Any], report["storage"])
    assert report["exit_code"] == STORAGE_ERROR_EXIT
    assert storage["error"]["code"] == "INTERNAL_ERROR"
    assert "private diagnostic" not in json.dumps(report)
    assert (storage["published_record"] is not None) == during_verification
