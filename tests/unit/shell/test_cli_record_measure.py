"""Native measurement capture retains exact inputs and independent outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import measure, record_capture
from underwrite.cli import record_measure as command
from underwrite.cli.main import main
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service import records

FIXTURES = repo_root(Path(__file__).resolve()) / "fixtures/examples/event-measurement"
ERROR_EXIT = 2
STORAGE_EXIT = 3


def inputs(tmp_path: Path) -> tuple[Path, Path]:
    paths = tmp_path / "observation.json", tmp_path / "policy.json"
    for path, name in zip(paths, ("read-input.json", "policy.json"), strict=True):
        path.write_bytes((FIXTURES / name).read_bytes())
    return paths


def invoke(
    observation: Path, policy: Path, store: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = main(
        [
            "records",
            "measure",
            "--observation",
            str(observation),
            "--policy",
            str(policy),
            "--root",
            str(store),
            "--id",
            "sample",
            "--json",
        ]
    )
    output = capsys.readouterr()
    return code, json.loads(output.out or output.err)


def ordinary(observation: Path, policy: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    measure.main(["--observation", str(observation), "--policy", str(policy), "--json"])
    output = capsys.readouterr()
    return json.loads(output.out or output.err)


def test_success_exact_sources_actual_read_and_readback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observation, policy = inputs(tmp_path)
    observation.write_bytes(b"\n " + observation.read_bytes() + b" \n")
    expected = ordinary(observation, policy, capsys)
    store = tmp_path / "store"
    code, report = invoke(observation, policy, store, capsys)
    assert code == report["exit_code"] == 0
    assert report["analysis"]["report"] == expected
    for role, path in (("observation", observation), ("policy", policy)):
        raw = path.read_bytes()
        assert report["sources"][role] == {
            "state": "captured",
            "raw_hash": digest_bytes(raw),
            "bytes": len(raw),
        }
        assert records.read_record_file(store, "sample", f"source/{role}.bin").data == raw
    saved = records.read_record_file(store, "sample", "analysis/read.json")
    assert saved.data == canonical_bytes(expected) + b"\n"
    invocation = json.loads(records.read_record_file(store, "sample", "invocation.json").data)
    assert invocation["sources"] == report["sources"]
    assert invocation["execution_mode"] == "in_process"
    assert invocation["aggregate_outcome_location"] == "command_output"
    manifest = records.read_record(store, "sample").manifest
    assert manifest["kind"] == "measurement.event"
    assert manifest["provenance"] == {"verified": False, "attestation_ref": None}
    assert report["storage"]["verification"]["files_checked"] == len((1, 2, 3, 4))


@pytest.mark.parametrize(
    ("obs", "pol", "phase"),
    [
        (b"broken", None, "analysis"),
        (b"{}", None, "acquisition"),
        (None, b"broken", "acquisition"),
        (None, None, "acquisition"),
        (b"{}", b"{}", "analysis"),
        (b"broken", b"broken", "analysis"),
    ],
)
def test_failure_precedence_matches_measure_and_retains_only_complete_sources(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    obs: bytes | None,
    pol: bytes | None,
    phase: str,
) -> None:
    observation, policy = tmp_path / "observation", tmp_path / "policy"
    for path, raw in ((observation, obs), (policy, pol)):
        if raw is not None:
            path.write_bytes(raw)
    expected = ordinary(observation, policy, capsys)
    store = tmp_path / "store"
    code, report = invoke(observation, policy, store, capsys)
    assert code == ERROR_EXIT
    assert report["analysis"] == {"state": "error", "phase": phase, "error": expected}
    count = 2
    for role, raw in (("observation", obs), ("policy", pol)):
        if raw is None:
            assert report["sources"][role]["state"] == "unavailable"
        else:
            count += 1
            assert records.read_record_file(store, "sample", f"source/{role}.bin").data == raw
    assert report["storage"]["verification"]["files_checked"] == count


def test_both_inputs_attempted_once_and_mutation_cannot_change_measurement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, policy = inputs(tmp_path)
    expected = ordinary(observation, policy, capsys)
    original = command.read_source
    captured: dict[Path, bytes] = {}

    def mutate(path: Path, *args: Any) -> bytes:
        assert path not in captured
        raw = original(path, *args)
        captured[path] = raw
        path.write_bytes(b"mutated")
        return raw

    monkeypatch.setattr(command, "read_source", mutate)
    store = tmp_path / "store"
    code, report = invoke(observation, policy, store, capsys)
    assert code == 0 and report["analysis"]["report"] == expected
    assert list(captured) == [observation, policy]
    for role, path in (("observation", observation), ("policy", policy)):
        assert (
            records.read_record_file(store, "sample", f"source/{role}.bin").data == captured[path]
        )


def test_oversized_source_has_no_partial_capture_and_policy_is_attempted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, policy = inputs(tmp_path)
    monkeypatch.setattr(measure, "OBSERVATION_MAX_BYTES", 1)
    store = tmp_path / "store"
    code, report = invoke(observation, policy, store, capsys)
    assert code == ERROR_EXIT
    assert report["sources"]["observation"]["state"] == "unavailable"
    assert report["sources"]["policy"]["state"] == "captured"
    assert report["analysis"]["error"]["code"] == "INPUT_LIMIT_EXCEEDED"
    assert not (store / "records/sample/files/source/observation.bin").exists()


def test_collision_keeps_analysis_and_never_overwrites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observation, policy = inputs(tmp_path)
    store = tmp_path / "store"
    _, first = invoke(observation, policy, store, capsys)
    digest = records.read_record(store, "sample").digest
    code, second = invoke(observation, policy, store, capsys)
    assert code == STORAGE_EXIT and second["analysis"] == first["analysis"]
    assert second["storage"]["error"]["code"] == "RECORD_EXISTS"
    assert records.read_record(store, "sample").digest == digest


@pytest.mark.parametrize("fault", ["contracts", "construction", "evaluation", "output", "read_cap"])
def test_configuration_and_output_faults_precede_storage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    observation, policy = inputs(tmp_path)
    store = tmp_path / "store"

    def fail(*args: Any) -> Any:
        raise SchemaConfigurationError("TEST_SCHEMA_FAILURE")

    class BrokenValidator:
        def validate(self, instance: object) -> None:
            raise RuntimeError("private schema failure")

    if fault == "contracts":
        monkeypatch.setattr(command, "packaged_validator", fail)
    elif fault == "construction":
        monkeypatch.setattr(measure, "measurement_validators", fail)
    elif fault == "evaluation":
        monkeypatch.setattr(
            measure, "measurement_validators", lambda: (BrokenValidator(), BrokenValidator())
        )
    else:
        monkeypatch.setattr(
            command, "OUTPUT_MAX_BYTES" if fault == "output" else "RECORD_READ_MAX_BYTES", 1
        )
    code, report = invoke(observation, policy, store, capsys)
    assert code == ERROR_EXIT and report["schema"] == "evidence_error.v1"
    assert report["code"] == (
        "OUTPUT_LIMIT_EXCEEDED" if fault in {"output", "read_cap"} else "SCHEMA_CONFIGURATION_ERROR"
    )
    assert not store.exists()


def test_verification_failure_preserves_read_and_published_identity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, policy = inputs(tmp_path)
    store = tmp_path / "store"

    def fail(root: Path, identifier: str) -> dict[str, object]:
        raise records.RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(record_capture, "verify_record", fail)
    code, report = invoke(observation, policy, store, capsys)
    assert code == STORAGE_EXIT and report["analysis"]["state"] == "result"
    assert (
        report["storage"]["published_record"]["manifest_digest"]
        == records.read_record(store, "sample").digest
    )
    assert (
        json.loads(records.read_record_file(store, "sample", "analysis/read.json").data)
        == report["analysis"]["report"]
    )


def test_withheld_read_is_saved_with_exit_zero_and_recovered_by_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observation, policy = inputs(tmp_path)
    payload = json.loads(observation.read_bytes())
    selector = payload["payload"]["measurement_semantics"]["outcome"]
    for reading in payload["payload"]["readings"]:
        if all(reading.get(key) == selector[key] for key in ("instrument", "metric_id")):
            reading["rows"].pop()
    observation.write_text(json.dumps(payload))
    store = tmp_path / "store"
    code, report = invoke(observation, policy, store, capsys)
    assert code == 0 and report["analysis"]["report"]["verdict"] == "not_measured"
    assert (
        main(
            [
                "records",
                "read-json",
                "--root",
                str(store),
                "--id",
                "sample",
                "--name",
                "analysis/read.json",
            ]
        )
        == 0
    )
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["content"] == report["analysis"]["report"]


@pytest.mark.parametrize("malformed", [False, True])
def test_storage_failure_preserves_analysis_without_claiming_record_absent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    malformed: bool,
) -> None:
    observation, policy = inputs(tmp_path)
    if malformed:
        observation.write_bytes(b"broken")
    store = tmp_path / "store"

    def fail(root: Path, spec: records.CapturedRecordSpec) -> records.RecordSnapshot:
        raise records.RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(record_capture, "save_captured_record", fail)
    code, report = invoke(observation, policy, store, capsys)
    assert code == STORAGE_EXIT
    assert report["analysis"]["state"] == ("error" if malformed else "result")
    assert report["storage"]["published_record"] is None
    assert not store.exists()


@pytest.mark.parametrize("as_json", [True, False])
def test_output_admission_reserves_every_storage_response_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    as_json: bool,
) -> None:
    from underwrite.cli.records import record_error_candidates

    observation, policy = inputs(tmp_path)
    report = command.run_record_measure(observation, policy, tmp_path / "reference", "sample")
    storage = cast(dict[str, Any], report["storage"])
    identity = {"record_id": "sample", "manifest_digest": storage["manifest_digest"]}
    variants = [report]
    for error in record_error_candidates():
        for known in (None, identity):
            variants.append(
                {
                    **report,
                    "exit_code": STORAGE_EXIT,
                    "storage": {
                        "state": "error",
                        "error": error,
                        "published_record": known,
                    },
                }
            )
    maximum = max(
        len(canonical_bytes(item) if as_json else command.render_human(item).encode()) + 1
        for item in variants
    )
    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", maximum)
    assert (
        command.run_record_measure(
            observation, policy, tmp_path / "accepted", "sample", as_json=as_json
        )["exit_code"]
        == 0
    )
    monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", maximum - 1)
    with pytest.raises(command.EvidenceError):
        command.run_record_measure(
            observation, policy, tmp_path / "refused", "sample", as_json=as_json
        )
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize("observation_raw", [b"broken", None])
def test_missing_policy_contract_prevents_diagnostic_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    observation_raw: bytes | None,
) -> None:
    from underwrite.instrument.ingest import schema as schema_owner

    package = tmp_path / "package"
    contracts = package / "_contracts"
    contracts.mkdir(parents=True)
    for source in (FIXTURES.parents[2] / "contracts").glob("*.schema.json"):
        if source.name != "measurement_policy.v3.schema.json":
            (contracts / source.name).write_bytes(source.read_bytes())
    def resources(name: str) -> Path:
        assert name == "underwrite"
        return package

    monkeypatch.setattr(schema_owner, "files", resources)
    observation, policy = tmp_path / "observation", tmp_path / "policy"
    if observation_raw is not None:
        observation.write_bytes(observation_raw)
    policy.write_bytes(b"{}")
    store = tmp_path / "store"
    code, report = invoke(observation, policy, store, capsys)
    assert code == ERROR_EXIT
    assert report["schema"] == "evidence_error.v1"
    assert report["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert not store.exists()
