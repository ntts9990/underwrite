"""No-profile Langfuse input chooses a supported reader family, not a release."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import record_capture
from underwrite.cli import record_langfuse as record_command
from underwrite.cli.main import main
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    BLOB_READER,
    CLI_READER,
    ROW_INDEX_SCHEMA,
)
from underwrite.instrument.ingest import langfuse_accounting as adapter
from underwrite.instrument.ingest import langfuse_compat as auto_adapter
from underwrite.instrument.ingest.application import DEFAULT_MAX_DEPTH
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service import records
from underwrite.service.records import read_record, read_record_file, verify_record

ROOT = repo_root(Path(__file__).resolve())
BLOB = ROOT / "fixtures/golden/external/langfuse"
CLI = ROOT / "fixtures/examples/langfuse-cli"
PAIRS = {
    "blob": (
        BLOB / "observations_v2-2026-09-14T06-40-00.json",
        BLOB / "scores-2026-09-14T06-40-00.json",
    ),
    "cli": (CLI / "observations.json", CLI / "scores.json"),
}
ERROR_EXIT = 2
STORAGE_EXIT = 3


def _invoke(
    capsys: pytest.CaptureFixture[str], observations: Path, scores: Path, *options: str
) -> tuple[int, dict[str, Any], str, str]:
    code = main(
        [
            "audit-langfuse",
            "--observations",
            str(observations),
            "--scores",
            str(scores),
            *options,
            "--json",
        ]
    )
    streams = capsys.readouterr()
    return code, json.loads(streams.out or streams.err), streams.out, streams.err


@pytest.mark.parametrize("family", ["blob", "cli"])
def test_no_profile_audits_actual_pair_without_release_claim(
    capsys: pytest.CaptureFixture[str], family: str
) -> None:
    observations, scores = PAIRS[family]
    code, report, stdout, stderr = _invoke(capsys, observations, scores)
    assert code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["schema"] == ACCOUNTING_SCHEMA
    assert report["reader_profile"] == (BLOB_READER if family == "blob" else CLI_READER)
    for role, path in (("observations", observations), ("scores", scores)):
        source = report["sources"][role]
        assert source["raw_hash"] == digest_bytes(path.read_bytes())
        assert source["producer_version"] is None
        assert source["producer_version_state"] == "unknown"
        assert "format_version" not in source
    assert report == adapter.audit_langfuse_bytes(observations.read_bytes(), scores.read_bytes())
    assert report == adapter.audit_langfuse_files(observations, scores)
    packaged_validator(ACCOUNTING_SCHEMA).validate(report)  # pyright: ignore[reportUnknownMemberType]
    if family == "cli":
        assert report["scores"][1]["value"] is False
        assert report["scores"][3]["value"] == "synthetic review"
        assert report["scores"][4]["data_type"] == "CORRECTION"


@pytest.mark.parametrize("family", ["blob", "cli"])
def test_no_profile_row_index_keeps_family_counts_without_source_content(
    capsys: pytest.CaptureFixture[str], family: str
) -> None:
    observations, scores = PAIRS[family]
    full_code, full, _, _ = _invoke(capsys, observations, scores)
    row_code, row, stdout, stderr = _invoke(capsys, observations, scores, "--view", "row-index")
    assert full_code == row_code == 0 and stderr == ""
    assert row["schema"] == ROW_INDEX_SCHEMA
    assert row["reader_profile"] == full["reader_profile"]
    assert row["sources"] == full["sources"] and row["counts"] == full["counts"]
    assert row == adapter.audit_langfuse_row_index_files(observations, scores)
    packaged_validator(ROW_INDEX_SCHEMA).validate(row)  # pyright: ignore[reportUnknownMemberType]
    assert "value" not in row["score_rows"][0]
    for private in ("score-1", "synthetic review", "underwrite-capture-project"):
        assert private not in stdout


@pytest.mark.parametrize(
    ("observation_name", "score_name", "truncated"),
    [
        ("observations-body.json", "scores-body.json", False),
        ("observations.json", "scores-truncated.json", True),
    ],
)
def test_no_profile_cli_body_and_partial_collection_keep_reported_scope(
    capsys: pytest.CaptureFixture[str], observation_name: str, score_name: str, truncated: bool
) -> None:
    code, report, _, stderr = _invoke(capsys, CLI / observation_name, CLI / score_name)
    assert code == 0 and stderr == ""
    assert report["reader_profile"] == CLI_READER
    assert report["retrieval"]["scores"]["pagination"]["truncated"] is truncated
    assert report["planned"]["missing_execution_count"] is None
    if observation_name.endswith("body.json"):
        assert report["retrieval"]["observations"]["capture_kind"] == "output_body"


def test_empty_arrays_are_a_reader_shape_without_an_origin_claim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observations, scores = tmp_path / "observations.json", tmp_path / "scores.json"
    observations.write_bytes(b"[]")
    scores.write_bytes(b"[]")
    code, report, _, stderr = _invoke(capsys, observations, scores)
    assert code == 0 and stderr == ""
    assert report["reader_profile"] == BLOB_READER
    assert report["counts"]["observations"] == report["counts"]["scores"] == 0
    assert all(source["producer_version"] is None for source in report["sources"].values())


def test_array_family_tolerates_additive_nonsemantic_columns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observations = json.loads(PAIRS["blob"][0].read_bytes())
    scores = json.loads(PAIRS["blob"][1].read_bytes())
    observations[0]["newUnconsumed"] = "PRIVATE_SENTINEL"
    scores[0]["newUnconsumed"] = "PRIVATE_SENTINEL"
    obs_path, score_path = tmp_path / "observations.json", tmp_path / "scores.json"
    obs_path.write_text(json.dumps(observations), encoding="utf-8")
    score_path.write_text(json.dumps(scores), encoding="utf-8")
    code, report, stdout, stderr = _invoke(capsys, obs_path, score_path)
    assert code == 0 and stderr == ""
    assert report["reader_profile"] == BLOB_READER
    assert report["counts"]["scores"] == len(scores)
    assert "PRIVATE_SENTINEL" not in stdout


@pytest.mark.parametrize("case", ["mixed", "api-data-array"])
def test_mixed_or_api_extracted_array_never_falls_back_to_blob_semantics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    if case == "mixed":
        observations, scores = PAIRS["blob"][0], PAIRS["cli"][1]
    else:
        observations, scores = tmp_path / "observations.json", tmp_path / "scores.json"
        obs = json.loads((CLI / "observations-body.json").read_bytes())["data"]
        sc = json.loads((CLI / "scores-body.json").read_bytes())["data"]
        observations.write_text(json.dumps(obs), encoding="utf-8")
        scores.write_text(json.dumps(sc), encoding="utf-8")
    code, error, stdout, stderr = _invoke(capsys, observations, scores)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "UNSUPPORTED_PROFILE"
    assert "project-synthetic" not in stderr


@pytest.mark.parametrize("alias", ["stringValue", "longStringValue"])
@pytest.mark.parametrize("view", ["accounting", "row-index"])
def test_conflicting_cli_text_value_channels_refuse_atomically(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], alias: str, view: str
) -> None:
    source = json.loads((CLI / "scores.json").read_bytes())
    source["body"]["data"][3][alias] = "PRIVATE_SENTINEL"
    score_path = tmp_path / "scores.json"
    score_path.write_text(json.dumps(source), encoding="utf-8")
    code, error, stdout, stderr = _invoke(
        capsys, CLI / "observations.json", score_path, "--view", view
    )
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "UNSUPPORTED_PROFILE" and error["location"] == "/scores/3"
    assert "PRIVATE_SENTINEL" not in stderr


def test_missing_cli_subject_stays_unknown_and_alternate_target_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = json.loads((CLI / "scores.json").read_bytes())
    source["body"]["data"][0]["subject"] = None
    del source["body"]["data"][1]["subject"]
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps(source), encoding="utf-8")
    code, report, _, stderr = _invoke(capsys, CLI / "observations.json", scores)
    assert code == 0 and stderr == ""
    assert report["counts"]["associations"]["subject_unknown"] == ERROR_EXIT
    assert report["scores"][0]["subject"]["availability"] == "null"
    assert report["scores"][1]["subject"]["availability"] == "absent"
    source["body"]["data"][0]["observationId"] = "PRIVATE_SENTINEL"
    scores.write_text(json.dumps(source), encoding="utf-8")
    code, error, stdout, stderr = _invoke(capsys, CLI / "observations.json", scores)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "UNSUPPORTED_PROFILE" and "PRIVATE_SENTINEL" not in stderr


def test_array_conflicting_target_refuses_auto_and_explicit_blob(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = json.loads(PAIRS["blob"][1].read_bytes())
    source[3]["session_id"] = "PRIVATE_SENTINEL"
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps(source), encoding="utf-8")
    for options in ((), ("--profile", "blob-4.35.0")):
        code, error, stdout, stderr = _invoke(capsys, PAIRS["blob"][0], scores, *options)
        assert code == ERROR_EXIT and stdout == ""
        assert error["code"] == "UNSUPPORTED_PROFILE"
        assert "PRIVATE_SENTINEL" not in stderr


@pytest.mark.parametrize("target", ["session_id", "dataset_run_id"])
def test_empty_other_target_is_absent_in_auto_and_explicit_blob(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    source = json.loads(PAIRS["blob"][1].read_bytes())
    source[0][target] = ""
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps(source), encoding="utf-8")
    auto_exit, auto, _, auto_error = _invoke(capsys, PAIRS["blob"][0], scores)
    old_exit, old, _, old_error = _invoke(
        capsys, PAIRS["blob"][0], scores, "--profile", "blob-4.35.0"
    )
    assert auto_exit == old_exit == 0 and auto_error == old_error == ""
    assert auto["schema"] == "langfuse_accounting.v2"
    assert old["schema"] == "langfuse_accounting.v1"
    assert auto["scores"][0]["association"] == old["scores"][0]["association"]
    assert (
        auto["scores"][0]["target_observation_index"]
        == old["scores"][0]["target_observation_index"]
    )


def test_auto_file_api_reads_each_source_once_and_matches_bytes_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations, scores = PAIRS["cli"]
    original = auto_adapter.read_langfuse_file
    reads: list[str] = []

    def tracked(path: Path, role: str) -> bytes:
        reads.append(role)
        return original(path, role)

    monkeypatch.setattr(auto_adapter, "read_langfuse_file", tracked)
    assert adapter.audit_langfuse_files(observations, scores) == (
        adapter.audit_langfuse_bytes(observations.read_bytes(), scores.read_bytes())
    )
    assert reads == ["observations", "scores"]


@pytest.mark.parametrize("family", ["blob", "cli"])
def test_direct_auto_profile_spelling_matches_omission(tmp_path: Path, family: str) -> None:
    observations, scores = PAIRS[family]
    raw_observations, raw_scores = observations.read_bytes(), scores.read_bytes()
    assert adapter.audit_langfuse_bytes(
        raw_observations, raw_scores, profile="auto"
    ) == adapter.audit_langfuse_bytes(raw_observations, raw_scores)
    assert adapter.audit_langfuse_files(
        observations, scores, profile="auto"
    ) == adapter.audit_langfuse_files(observations, scores)
    assert adapter.audit_langfuse_row_index_files(
        observations, scores, profile="auto"
    ) == adapter.audit_langfuse_row_index_files(observations, scores)
    explicit = cast(
        dict[str, Any],
        record_command.run_record_langfuse(
            observations, scores, tmp_path / "explicit-auto", "sample", profile="auto"
        ),
    )
    omitted = cast(
        dict[str, Any],
        record_command.run_record_langfuse(observations, scores, tmp_path / "omitted", "sample"),
    )
    assert explicit["schema"] == omitted["schema"] == record_command.AUTO_RECORD_SCHEMA
    assert explicit["analysis"] == omitted["analysis"]
    assert explicit["sources"] == omitted["sources"]
    for root in (tmp_path / "explicit-auto", tmp_path / "omitted"):
        manifest = cast(dict[str, Any], read_record(root, "sample").manifest)
        assert manifest["metadata"]["selection_mode"] == "auto"
        assert (
            manifest["metadata"]["reader_profile"]
            == explicit["analysis"]["report"]["reader_profile"]
        )


def test_auto_depth_refusal_uses_shared_decoder_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observations = tmp_path / "PRIVATE_SENTINEL.json"
    depth = DEFAULT_MAX_DEPTH + 1
    observations.write_bytes(b"[" * depth + b"0" + b"]" * depth)
    code, error, stdout, stderr = _invoke(capsys, observations, PAIRS["blob"][1])
    assert code == ERROR_EXIT and stdout == ""
    assert (error["code"], error["reason"], error["location"]) == (
        "INPUT_LIMIT_EXCEEDED",
        "DEPTH_LIMIT_EXCEEDED",
        "/observations",
    )
    assert "PRIVATE_SENTINEL" not in stderr


def test_auto_packaged_contract_failure_precedes_input_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(_: str) -> Any:
        raise SchemaConfigurationError("PRIVATE_SENTINEL")

    def forbidden(_: Path, __: str) -> bytes:
        pytest.fail("source read before v2 contract")

    monkeypatch.setattr(auto_adapter, "packaged_validator", missing)
    monkeypatch.setattr(auto_adapter, "read_langfuse_file", forbidden)
    with pytest.raises(adapter.LangfuseFileError) as caught:
        adapter.audit_langfuse_files(tmp_path / "obs", tmp_path / "scores")
    assert (caught.value.code, caught.value.reason) == (
        "SCHEMA_CONFIGURATION_ERROR",
        "INVALID_PACKAGED_CONTRACT",
    )


@pytest.mark.parametrize("family", ["blob", "cli"])
def test_auto_record_saves_one_pair_and_recovers_after_original_deletion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], family: str
) -> None:
    originals = PAIRS[family]
    paths = {role: tmp_path / (role + ".json") for role in ("observations", "scores")}
    for role, source in zip(("observations", "scores"), originals, strict=True):
        paths[role].write_bytes(source.read_bytes())
    store = tmp_path / "store"
    code = main(
        [
            "records",
            "audit-langfuse",
            "--observations",
            str(paths["observations"]),
            "--scores",
            str(paths["scores"]),
            "--root",
            str(store),
            "--id",
            "sample",
            "--json",
        ]
    )
    streams = capsys.readouterr()
    assert code == 0 and streams.err == ""
    record: dict[str, Any] = json.loads(streams.out)
    assert streams.out.encode() == canonical_bytes(record) + b"\n"
    assert record["schema"] == record_command.AUTO_RECORD_SCHEMA
    report = cast(
        dict[str, Any],
        adapter.audit_langfuse_bytes(
            paths["observations"].read_bytes(), paths["scores"].read_bytes()
        ),
    )
    assert record["analysis"] == {"state": "result", "report": report}
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["selection_mode"] == "auto"
    assert invocation["reader_profile"] == report["reader_profile"]
    manifest = cast(dict[str, Any], read_record(store, "sample").manifest)
    assert manifest["metadata"]["selection_mode"] == "auto"
    assert manifest["metadata"]["reader_profile"] == report["reader_profile"]
    assert verify_record(store, "sample")["valid"] is True
    for role, path in paths.items():
        raw = path.read_bytes()
        assert report["sources"][role]["raw_hash"] == digest_bytes(raw)
        assert read_record_file(store, "sample", f"source/{role}.bin").data == raw
        path.unlink()
        assert read_record_file(store, "sample", f"source/{role}.bin").data == raw
    assert str(tmp_path) not in streams.out


def test_auto_record_complete_mixed_pair_saves_both_bytes_without_applied_reader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observations, scores = tmp_path / "observations.json", tmp_path / "scores.json"
    observations.write_bytes(PAIRS["blob"][0].read_bytes())
    scores.write_bytes(PAIRS["cli"][1].read_bytes())
    store = tmp_path / "store"
    argv = [
        "records",
        "audit-langfuse",
        "--observations",
        str(observations),
        "--scores",
        str(scores),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    ]
    assert main(argv) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.err == ""
    record: dict[str, Any] = json.loads(streams.out)
    assert record["analysis"]["phase"] == "analysis"
    assert record["analysis"]["error"]["reason"] == "LANGFUSE_AUTO_MIXED_FAMILIES"
    assert record["storage"]["state"] == "saved"
    for role, path in (("observations", observations), ("scores", scores)):
        assert record["sources"][role]["raw_hash"] == digest_bytes(path.read_bytes())
        assert read_record_file(store, "sample", f"source/{role}.bin").data == path.read_bytes()
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["selection_mode"] == "auto" and "reader_profile" not in invocation
    metadata = cast(dict[str, Any], read_record(store, "sample").manifest)["metadata"]
    assert metadata["selection_mode"] == "auto" and "reader_profile" not in metadata
    assert main(argv) == STORAGE_EXIT
    assert json.loads(capsys.readouterr().out)["storage"]["error"]["code"] == "RECORD_EXISTS"


def test_auto_record_attempts_both_roles_when_one_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observations, scores = tmp_path / "observations.json", tmp_path / "missing-scores.json"
    raw = PAIRS["blob"][0].read_bytes()
    observations.write_bytes(raw)
    original = record_command.read_langfuse_file
    attempted: list[str] = []

    def tracked(path: Path, role: str) -> bytes:
        attempted.append(role)
        return original(path, role)

    monkeypatch.setattr(record_command, "read_langfuse_file", tracked)
    store = tmp_path / "store"
    record = cast(
        dict[str, Any],
        record_command.run_record_langfuse(observations, scores, store, "sample"),
    )
    assert attempted == ["observations", "scores"]
    assert record["exit_code"] == ERROR_EXIT
    assert record["analysis"]["phase"] == "acquisition"
    assert record["sources"]["observations"]["raw_hash"] == digest_bytes(raw)
    assert record["sources"]["scores"]["state"] == "unavailable"
    assert read_record_file(store, "sample", "source/observations.bin").data == raw
    assert not (store / "records/sample/files/source/scores.bin").exists()
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert "reader_profile" not in invocation


def test_auto_record_uses_only_each_captured_buffer_after_source_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = {role: tmp_path / f"{role}.json" for role in ("observations", "scores")}
    originals = {
        role: source.read_bytes() for role, source in zip(paths, PAIRS["cli"], strict=True)
    }
    for role, path in paths.items():
        path.write_bytes(originals[role])
    original_read = record_command.read_langfuse_file
    reads: list[str] = []

    def replace(path: Path, role: str) -> bytes:
        reads.append(role)
        captured = original_read(path, role)
        path.write_bytes(b"changed after capture")
        return captured

    monkeypatch.setattr(record_command, "read_langfuse_file", replace)
    store = tmp_path / "store"
    result = cast(
        dict[str, Any],
        record_command.run_record_langfuse(paths["observations"], paths["scores"], store, "sample"),
    )
    assert result["exit_code"] == 0 and reads == ["observations", "scores"]
    for role, raw in originals.items():
        assert result["analysis"]["report"]["sources"][role]["raw_hash"] == digest_bytes(raw)
        assert read_record_file(store, "sample", f"source/{role}.bin").data == raw


def test_auto_record_schema_and_id_refuse_before_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(_: Path, __: str) -> bytes:
        pytest.fail("source read before selected schema or ID")

    monkeypatch.setattr(record_command, "read_langfuse_file", forbidden)
    store = tmp_path / "store"
    with pytest.raises(record_command.EvidenceError):
        record_command.run_record_langfuse(tmp_path / "obs", tmp_path / "scores", store, "../bad")

    def missing(name: str) -> Any:
        if name == record_command.AUTO_RECORD_SCHEMA:
            raise SchemaConfigurationError("PRIVATE_SENTINEL")
        return original(name)

    original = record_command.packaged_validator
    monkeypatch.setattr(record_command, "packaged_validator", missing)
    with pytest.raises(SchemaConfigurationError):
        record_command.run_record_langfuse(tmp_path / "obs", tmp_path / "scores", store, "sample")
    assert not store.exists()


def test_auto_record_output_admission_precedes_storage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    observations, scores = PAIRS["cli"]
    store = tmp_path / "store"

    def forbidden(root: Path, spec: records.CapturedRecordSpec) -> records.RecordSnapshot:
        del root, spec
        pytest.fail("storage started before response admission")

    monkeypatch.setattr(record_command, "OUTPUT_MAX_BYTES", 1)
    monkeypatch.setattr(record_capture, "save_captured_record", forbidden)
    assert (
        main(
            [
                "records",
                "audit-langfuse",
                "--observations",
                str(observations),
                "--scores",
                str(scores),
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
    assert streams.out == "" and json.loads(streams.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert not store.exists()


def test_auto_record_postpublication_error_returns_no_certain_absence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    observations, scores = PAIRS["cli"]
    store = tmp_path / "store"
    original = records._fsync_dir  # pyright: ignore[reportPrivateUsage]

    def fail_after_manifest(directory: Path) -> None:
        if directory == store / "records/sample" and (directory / "manifest.json").exists():
            raise OSError("private postpublication fault")
        original(directory)

    monkeypatch.setattr(records, "_fsync_dir", fail_after_manifest)
    assert (
        main(
            [
                "records",
                "audit-langfuse",
                "--observations",
                str(observations),
                "--scores",
                str(scores),
                "--root",
                str(store),
                "--id",
                "sample",
                "--json",
            ]
        )
        == STORAGE_EXIT
    )
    streams = capsys.readouterr()
    assert streams.err == "" and streams.out
    record = json.loads(streams.out)
    assert record["analysis"]["state"] == "result"
    assert record["storage"]["published_record"] is None
    assert (store / "records/sample/manifest.json").exists()
