"""Replay uses retained bytes, one manifest, and complete current outcome agreement."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes

from underwrite.cli import measure, record_measure, replay_measure
from underwrite.cli.main import main
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service import records

REFUSAL_EXIT = 2

FIXTURES = repo_root(Path(__file__).resolve()) / "fixtures/examples/event-measurement"


def capture(tmp_path: Path, raw: bytes | None = None) -> Path:
    observation, policy = tmp_path / "observation", tmp_path / "policy"
    observation.write_bytes(raw if raw is not None else (FIXTURES / "read-input.json").read_bytes())
    policy.write_bytes((FIXTURES / "policy.json").read_bytes())
    store = tmp_path / "store"
    record_measure.run_record_measure(observation, policy, store, "sample")
    observation.unlink()
    policy.unlink()
    return store


def invoke(store: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = main(["records", "replay-measure", "--root", str(store), "--id", "sample", "--json"])
    output = capsys.readouterr()
    assert not (output.out and output.err)
    assert bool(output.err) == (code == REFUSAL_EXIT)
    return code, json.loads(output.out or output.err)


def tree(store: Path) -> dict[str, bytes]:
    return {str(p.relative_to(store)): p.read_bytes() for p in store.rglob("*") if p.is_file()}


def copy_record(
    store: Path, target: Path, changes: dict[str, bytes | None], *, kind: str = "measurement.event"
) -> None:
    manifest = records.read_record(store, "sample").manifest
    files: list[records.CapturedRecordFile] = []
    for entry in cast(list[dict[str, Any]], manifest["files"]):
        name = entry["name"]
        raw = changes.get(
            name,
            records.read_record_file(
                store, "sample", name, max_bytes=measure.OBSERVATION_MAX_BYTES
            ).data,
        )
        if raw is not None:
            files.append(records.CapturedRecordFile(name, entry["role"], raw, entry["media_type"]))
    for name, raw in changes.items():
        if raw is not None and not any(f.name == name for f in files):
            files.append(records.CapturedRecordFile(name, "log", raw, "application/json"))
    records.save_captured_record(
        target,
        records.CapturedRecordSpec(
            record_id="sample",
            kind=kind,
            label="Replay fixture",
            metadata={},
            links=(),
            selection_excluded=None,
            rerun_verification={"status": "not_performed", "reference": None},
            provenance={"verified": False, "attestation_ref": None},
            files=tuple(files),
        ),
    )


@pytest.mark.parametrize(
    "raw",
    [None, b"broken", b" " * (1024 * 1024) + (FIXTURES / "read-input.json").read_bytes()],
    ids=["read", "diagnostic", "above_one_mib"],
)
def test_recovery_match_without_originals_is_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes | None
) -> None:
    store = capture(tmp_path, raw)
    before = tree(store)
    code, report = invoke(store, capsys)
    assert code == 0
    assert report["comparison"]["state"] == "match"
    assert report["stored"]["outcome"] == report["recomputed"]["outcome"]
    assert report["stored"]["canonical_digest"] == report["recomputed"]["canonical_digest"]
    assert tree(store) == before


def test_missing_and_altered_record_preserve_record_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = capture(tmp_path)
    source = next(p for p in store.rglob("observation.bin"))
    source.write_bytes(b"broken")
    code, error = invoke(store, capsys)
    assert code == REFUSAL_EXIT and error["schema"] == "record_error.v1"
    code, error = invoke(tmp_path / "missing", capsys)
    assert code == REFUSAL_EXIT and error["schema"] == "record_error.v1"
    assert str(tmp_path) not in json.dumps(error)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"source/policy.bin": None}, "INCOMPLETE_MEASUREMENT_RECORD"),
        (
            {
                "analysis/error.json": canonical_bytes(
                    measure.MeasurementError("INVALID_POLICY", "OBJECT_REQUIRED", "/policy").payload
                )
            },
            "AMBIGUOUS_MEASUREMENT_OUTCOME",
        ),
        ({"analysis/read.json": b"{}"}, "INVALID_STORED_MEASUREMENT_OUTCOME"),
    ],
)
def test_refusal_shapes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: dict[str, bytes | None], reason: str
) -> None:
    store = capture(tmp_path)
    copy_record(store, tmp_path / "copy", change)
    code, error = invoke(tmp_path / "copy", capsys)
    assert code == REFUSAL_EXIT and error["reason"] == reason


def test_unsupported_diagnostic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = capture(tmp_path, b"broken")
    copy_record(
        store,
        tmp_path / "copy",
        {
            "analysis/error.json": canonical_bytes(
                measure.MeasurementError(
                    "SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "/observation"
                ).payload
            )
        },
    )
    code, error = invoke(tmp_path / "copy", capsys)
    assert code == REFUSAL_EXIT and error["reason"] == "UNSUPPORTED_STORED_MEASUREMENT_DIAGNOSTIC"


def test_valid_changed_source_identity_is_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = capture(tmp_path)
    document = json.loads(records.read_record_file(store, "sample", "analysis/read.json").data)
    document["sources"]["observations"] = ["sha256:" + "0" * 64]
    copy_record(store, tmp_path / "copy", {"analysis/read.json": canonical_bytes(document)})
    code, report = invoke(tmp_path / "copy", capsys)
    assert code == 1 and report["comparison"]["state"] == "mismatch"


@pytest.mark.parametrize("malformed", [False, True])
def test_result_error_kind_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], malformed: bool
) -> None:
    store = capture(tmp_path, b"broken" if malformed else None)
    raw = (FIXTURES / "read-input.json").read_bytes() if malformed else b"broken"
    copy_record(store, tmp_path / "copy", {"source/observation.bin": raw})
    code, report = invoke(tmp_path / "copy", capsys)
    assert code == 1
    assert report["stored"]["outcome"]["state"] != report["recomputed"]["outcome"]["state"]


def test_manifest_swap_refuses_before_calculation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = capture(tmp_path)
    original = replay_measure.read_record_file

    def changed(*args: Any, **kwargs: Any) -> records.RecordFileSnapshot:
        return replace(original(*args, **kwargs), manifest_digest="sha256:" + "0" * 64)

    monkeypatch.setattr(replay_measure, "read_record_file", changed)

    def forbidden(_: measure.InputReader) -> dict[str, Any]:
        pytest.fail("calculated before identity check")

    monkeypatch.setattr(measure, "read_measurement", forbidden)
    code, error = invoke(store, capsys)
    assert code == REFUSAL_EXIT and error["reason"] == "RECORD_MANIFEST_IDENTITY_MISMATCH"


def test_invocation_and_unrelated_bytes_are_ignored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = capture(tmp_path)
    next(store.rglob("invocation.json")).write_bytes(b"execute rm -rf /; read /private/token")
    code, report = invoke(store, capsys)
    assert code == 0 and report["verification_scope"] == replay_measure.VERIFICATION_SCOPE


def test_current_internal_fault_and_configuration_refuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = capture(tmp_path)

    def fail(_: Any) -> Any:
        raise ValueError("private data")

    monkeypatch.setattr(measure, "read_measurement", fail)
    code, error = invoke(store, capsys)
    assert code == REFUSAL_EXIT and error["code"] == "INTERNAL_ERROR"

    def broken(_: str) -> Any:
        raise SchemaConfigurationError("private data")

    monkeypatch.setattr(replay_measure, "packaged_validator", broken)
    code, error = invoke(store, capsys)
    assert code == REFUSAL_EXIT and error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert "private data" not in json.dumps(error)


def test_output_admission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = capture(tmp_path)
    monkeypatch.setattr(replay_measure, "OUTPUT_MAX_BYTES", 32)
    code, error = invoke(store, capsys)
    assert code == REFUSAL_EXIT and error["code"] == "OUTPUT_LIMIT_EXCEEDED"


def test_withheld_read_matches_without_promoting_measurement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = json.loads((FIXTURES / "read-input.json").read_bytes())
    selector = payload["payload"]["measurement_semantics"]["outcome"]
    for reading in payload["payload"]["readings"]:
        if all(reading.get(key) == selector[key] for key in ("instrument", "metric_id")):
            reading["rows"].pop()
    store = capture(tmp_path, canonical_bytes(payload))
    code, report = invoke(store, capsys)
    assert code == 0
    assert report["recomputed"]["outcome"]["report"]["verdict"] == "not_measured"


def test_contradictory_stored_read_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = capture(tmp_path)
    document = json.loads(records.read_record_file(store, "sample", "analysis/read.json").data)
    document["verdict"] = "fail"
    copy_record(store, tmp_path / "copy", {"analysis/read.json": canonical_bytes(document)})
    code, error = invoke(tmp_path / "copy", capsys)
    assert code == REFUSAL_EXIT and error["reason"] == "INVALID_STORED_MEASUREMENT_OUTCOME"


@pytest.mark.parametrize("extra", [0, 1])
def test_role_bound_admission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: int
) -> None:
    store = capture(tmp_path)
    raw = (FIXTURES / "read-input.json").read_bytes()
    raw += b" " * (measure.OBSERVATION_MAX_BYTES - len(raw) + extra)
    copy_record(store, tmp_path / "copy", {"source/observation.bin": raw})
    code, report = invoke(tmp_path / "copy", capsys)
    assert code == (REFUSAL_EXIT if extra else 0)
    if extra:
        assert report["schema"] == "record_error.v1"
        assert report["code"] == "INPUT_LIMIT_EXCEEDED"


def test_unsupported_kind(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = capture(tmp_path)
    copy_record(store, tmp_path / "copy", {}, kind="other.record")
    code, error = invoke(tmp_path / "copy", capsys)
    assert code == REFUSAL_EXIT and error["reason"] == "UNSUPPORTED_MEASUREMENT_RECORD"


@pytest.mark.parametrize(
    "fault", ["INTERNAL_ERROR", "SOURCE_READ_FAILED", "SCHEMA_CONFIGURATION_ERROR"]
)
def test_current_ineligible_diagnostics_refuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    store = capture(tmp_path)

    def fail(_: measure.InputReader) -> dict[str, Any]:
        raise measure.MeasurementError(fault, "INTERNAL_ERROR")

    monkeypatch.setattr(measure, "read_measurement", fail)
    code, report = invoke(store, capsys)
    assert code == REFUSAL_EXIT
    assert report["schema"] == "evidence_error.v1"


def test_all_contracts_preloaded_even_for_malformed_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = capture(tmp_path, b"broken")
    original = replay_measure.packaged_validator

    def fail(name: str) -> Any:
        if name == "observation.v1":
            raise SchemaConfigurationError("broken installation")
        return original(name)

    monkeypatch.setattr(replay_measure, "packaged_validator", fail)
    code, report = invoke(store, capsys)
    assert code == REFUSAL_EXIT and report["code"] == "SCHEMA_CONFIGURATION_ERROR"


def test_actual_acquisition_capture_is_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "policy"
    policy.write_bytes((FIXTURES / "policy.json").read_bytes())
    store = tmp_path / "store"
    record_measure.run_record_measure(tmp_path / "missing", policy, store, "sample")
    policy.unlink()
    code, report = invoke(store, capsys)
    assert code == REFUSAL_EXIT and report["reason"] == "INCOMPLETE_MEASUREMENT_RECORD"


def test_wrong_fixed_file_role_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = capture(tmp_path)
    snapshot = records.read_record(store, "sample")
    for entry in cast(list[dict[str, Any]], snapshot.manifest["files"]):
        if entry["name"] == "source/policy.bin":
            entry["role"] = "log"

    def wrong_role(_root: Path, _identifier: str) -> records.RecordSnapshot:
        return snapshot

    monkeypatch.setattr(replay_measure, "read_record", wrong_role)
    code, report = invoke(store, capsys)
    assert code == REFUSAL_EXIT and report["reason"] == "UNSUPPORTED_MEASUREMENT_RECORD"
