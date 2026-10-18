"""Two-file Langfuse accounting preserves source identity and original joins."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes, content_digest, digest_bytes

from underwrite.cli import audit_langfuse, capabilities, main
from underwrite.cli.evidence import ReportOutput, run_cli
from underwrite.instrument.ingest import langfuse_accounting as adapter
from underwrite.instrument.ingest import transport
from underwrite.instrument.ingest.application import packaged_ingestor
from underwrite.instrument.ingest.project import Source

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "fixtures/golden/external/langfuse"
ERROR_EXIT = 2


def inputs(tmp_path: Path) -> tuple[dict[str, Path], dict[str, list[dict[str, Any]]]]:
    rows = {
        "observations": json.loads(
            (GOLDEN / "observations_v2-2026-09-14T06-40-00.json").read_bytes()
        ),
        "scores": json.loads((GOLDEN / "scores-2026-09-14T06-40-00.json").read_bytes()),
    }
    paths = {role: tmp_path / (role + ".json") for role in rows}
    for role, path in paths.items():
        path.write_text(json.dumps(rows[role]), encoding="utf-8")
    return paths, rows


def argv(paths: dict[str, Path]) -> list[str]:
    return [
        "--observations",
        str(paths["observations"]),
        "--scores",
        str(paths["scores"]),
        "--profile",
        adapter.BLOB_PROFILE,
    ]


def invoke(
    paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any], str, str]:
    code = audit_langfuse.main([*argv(paths), "--json"])
    text = capsys.readouterr()
    return code, json.loads(text.out if code == 0 else text.err), text.out, text.err


def test_supplied_scores_keep_kinds_associations_and_two_raw_identities(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, _ = inputs(tmp_path)
    before = {role: path.read_bytes() for role, path in paths.items()}
    code, report, stdout, stderr = invoke(paths, capsys)
    assert code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["counts"] == {
        "observations": 3,
        "scores": 5,
        "by_data_type": {"NUMERIC": 2, "BOOLEAN": 1, "CATEGORICAL": 1, "TEXT": 1},
        "associations": {
            "observation_local": 3,
            "trace_level": 2,
            "unresolved_observation_reference": 0,
        },
        "observations_without_local_scores": 1,
    }
    assert report["planned"] == {
        "status": "unknown",
        "count": None,
        "missing_execution_count": None,
    }
    for role, path in paths.items():
        identity = report["sources"][role]
        assert identity["raw_hash"] == digest_bytes(before[role])
        assert identity["claims_verified"] is False
        observed = (
            packaged_ingestor(max_bytes=len(before[role]), max_depth=64)
            .file(path, Source(*adapter.SOURCES[role], None))
            .observation
        )
        assert identity["observation_digest"] == content_digest(observed)
        assert path.read_bytes() == before[role]
    by_name = {row["name"]: row for row in report["scores"]}
    assert by_name["relevance"]["value"] == 0 and by_name["relevance"]["string_value"] == "high"
    assert by_name["reviewer_note"]["association"] == "trace_level"
    assert by_name["reviewer_note"]["target_observation_index"] is None
    assert by_name["is_correct"]["value"] == 1 and type(by_name["is_correct"]["value"]) is int
    assert "task_success" not in by_name["is_correct"]
    assert str(tmp_path) not in stdout


def test_each_source_is_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = inputs(tmp_path)
    original = adapter.read_local_file
    reads: list[Path] = []

    def read(path: Path, maximum: int) -> bytes:
        reads.append(path)
        return original(path, maximum)

    monkeypatch.setattr(adapter, "read_local_file", read)
    adapter.audit_langfuse_files(
        paths["observations"], paths["scores"], profile=adapter.BLOB_PROFILE
    )
    assert reads == [paths["observations"], paths["scores"]]


def test_decomposed_original_id_is_not_silently_normalized_into_a_join(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, rows = inputs(tmp_path)
    rows["observations"][0]["id"] = "cafe\u0301"
    rows["scores"][0]["observation_id"] = "café"
    for role, path in paths.items():
        path.write_text(json.dumps(rows[role]), encoding="utf-8")
    code, error, stdout, _ = invoke(paths, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "UNSUPPORTED_PROFILE"
    assert error["reason"] == "LANGFUSE_NONCANONICAL_IDENTIFIER"


def test_display_text_normalizes_without_changing_null_or_row_coordinate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, rows = inputs(tmp_path)
    rows["scores"][0]["name"] = "cafe\u0301"
    rows["scores"][0]["string_value"] = "re\u0301sultat"
    paths["scores"].write_text(json.dumps(rows["scores"]), encoding="utf-8")
    code, report, _, _ = invoke(paths, capsys)
    assert code == 0
    score = report["scores"][0]
    assert score["score_index"] == 0 and score["name"] == "café"
    assert score["string_value"] == "résultat"
    assert report["scores"][-1]["string_value"] is None
    assert report["scope"]["display_text"] == "nfc_normalized"


def test_unresolved_reference_is_a_descriptive_success_not_a_failed_evaluation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, rows = inputs(tmp_path)
    rows["scores"][0]["project_id"] = "different-project"
    paths["scores"].write_text(json.dumps(rows["scores"]), encoding="utf-8")
    code, report, _, stderr = invoke(paths, capsys)
    assert code == 0 and stderr == ""
    assert report["scores"][0]["association"] == "unresolved_observation_reference"
    assert report["scores"][0]["target_observation_index"] is None
    assert "INSPECT_UNRESOLVED_OBSERVATION_REFERENCES" in report["next_actions"]
    assert report["planned"]["missing_execution_count"] is None


@pytest.mark.parametrize("role", ["observations", "scores"])
@pytest.mark.parametrize("raw", [b"\xff", b"[", b'[{"id":1,"id":2}]'])
def test_decode_errors_are_typed_and_have_role_only_locations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], role: str, raw: bytes
) -> None:
    paths, _ = inputs(tmp_path)
    paths[role].write_bytes(raw)
    code, error, stdout, stderr = invoke(paths, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INVALID_INPUT" and error["location"] == "/" + role
    assert str(tmp_path) not in stderr


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_nonregular_input_is_refused_without_waiting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    paths, _ = inputs(tmp_path)
    paths["scores"].unlink()
    if kind == "directory":
        paths["scores"].mkdir()
    elif kind == "fifo":
        os.mkfifo(paths["scores"])
    code, error, stdout, _ = invoke(paths, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "SOURCE_READ_FAILED" and error["location"] == "/scores"


def test_public_reader_platform_reason_is_not_sent_to_internal_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = inputs(tmp_path)

    def unsupported(path: Path, maximum: int) -> bytes:
        del path, maximum
        raise transport.TransportError("UNSUPPORTED_PLATFORM")

    monkeypatch.setattr(adapter, "read_local_file", unsupported)
    code, error, stdout, _ = invoke(paths, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == error["reason"] == "UNSUPPORTED_PLATFORM"


@pytest.mark.parametrize("axis", ["bytes", "depth"])
def test_existing_input_limits_are_not_relaxed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, axis: str
) -> None:
    paths, _ = inputs(tmp_path)
    monkeypatch.setattr(adapter, "DEFAULT_MAX_BYTES" if axis == "bytes" else "DEFAULT_MAX_DEPTH", 1)
    code, error, stdout, _ = invoke(paths, capsys)
    assert code == ERROR_EXIT and stdout == "" and error["code"] == "INPUT_LIMIT_EXCEEDED"


def test_result_limit_is_a_typed_refusal_without_partial_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, _ = inputs(tmp_path)
    code = run_cli(
        "audit-langfuse",
        [*argv(paths), "--json"],
        audit_langfuse.configure,
        audit_langfuse.produce,
        ReportOutput("langfuse_accounting.v1", max_bytes=1),
    )
    streams = capsys.readouterr()
    assert code == ERROR_EXIT and streams.out == ""
    assert json.loads(streams.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"


def test_human_counts_do_not_render_source_terminal_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, rows = inputs(tmp_path)
    rows["scores"][0]["name"] = "\x1b[31msource"
    paths["scores"].write_text(json.dumps(rows["scores"]), encoding="utf-8")
    assert audit_langfuse.main(argv(paths)) == 0
    text = capsys.readouterr().out
    assert "3 observations; 5 scores" in text
    assert "trace-level 2" in text and "unresolved 0" in text
    assert "Planned evaluations and export completeness unknown" in text
    assert "\x1b" not in text


def test_both_role_arguments_are_required(capsys: pytest.CaptureFixture[str]) -> None:
    assert audit_langfuse.main(["--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and json.loads(streams.err)["code"] == "USAGE_ERROR"


@pytest.mark.parametrize("value", [None, True, "0"])
def test_score_storage_value_is_not_coerced_from_another_json_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], value: object
) -> None:
    paths, rows = inputs(tmp_path)
    rows["scores"][0]["value"] = value
    paths["scores"].write_text(json.dumps(rows["scores"]), encoding="utf-8")
    code, error, stdout, _ = invoke(paths, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INVALID_INPUT"
    assert error["reason"] == "MALFORMED_LANGFUSE_PAYLOAD"


def test_empty_observation_export_does_not_reclassify_trace_scores(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, _ = inputs(tmp_path)
    paths["observations"].write_text("[]", encoding="utf-8")
    code, report, _, stderr = invoke(paths, capsys)
    assert code == 0 and stderr == ""
    assert report["counts"]["associations"] == {
        "observation_local": 0,
        "trace_level": 2,
        "unresolved_observation_reference": 3,
    }
    assert report["planned"] == {
        "status": "unknown",
        "count": None,
        "missing_execution_count": None,
    }


def test_broken_packaged_observation_schema_remains_a_configuration_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from underwrite.instrument.ingest.schema import SchemaConfigurationError

    paths, _ = inputs(tmp_path)

    def fail(*, max_bytes: int, max_depth: int) -> Any:
        del max_bytes, max_depth
        raise SchemaConfigurationError("INVALID_OBSERVATION_SCHEMA")

    monkeypatch.setattr(adapter, "packaged_ingestor", fail)
    code, error, stdout, stderr = invoke(paths, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert error["location"] == "" and str(tmp_path) not in stderr


def test_explicit_blob_json_bytes_remain_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    paths = {
        "observations": GOLDEN / "observations_v2-2026-09-14T06-40-00.json",
        "scores": GOLDEN / "scores-2026-09-14T06-40-00.json",
    }
    assert audit_langfuse.main([*argv(paths), "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    assert hashlib.sha256(streams.out.encode()).hexdigest() == (
        "472f74aee6f2c5aa4b3f104a5aba741254ca99eaa5d93fdcc64ad29373879bc7"
    )


def test_row_index_capability_profile() -> None:
    registered = capabilities.report(main.COMMAND_DESCRIPTORS)
    commands = cast(list[dict[str, Any]], registered["commands"])
    command = next(row for row in commands if row["command"] == "audit-langfuse")
    profile = next(row for row in command["profiles"] if row["name"] == "langfuse.row-index.v1")
    assert profile["required_inputs"] == ["observations", "scores", "profile", "view"]
    assert profile["output_schemas"] == ["langfuse_row_index.v1"]


def test_row_index_canary_and_entrypoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "PRIVATE_AGENT_CANARY"
    (tmp_path / canary).mkdir()
    paths, rows = inputs(tmp_path / canary)
    observation = rows["observations"][1]
    original_id = observation["id"]
    observation["id"] = canary
    observation["project_id"] = canary
    observation["trace_id"] = canary
    observation["input"] = canary
    for score in rows["scores"]:
        score["project_id"] = canary
        score["trace_id"] = canary
        score["id"] = canary
        score["name"] = canary
        score["string_value"] = canary
        if score["observation_id"] == original_id:
            score["observation_id"] = canary
    # Distinguish a local target, an unresolved reference, and trace-level rows.
    rows["scores"][2]["observation_id"] = canary + "-unresolved"
    rows["scores"][0]["value"] = 0
    rows["scores"][0]["string_value"] = None
    rows["scores"][1]["string_value"] = ""
    for role, path in paths.items():
        path.write_text(json.dumps(rows[role]), encoding="utf-8")

    assert main.main(["audit-langfuse", *argv(paths), "--view", "row-index", "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == "" and canary not in streams.out
    result = json.loads(streams.out)
    assert result == adapter.audit_langfuse_row_index_files(
        paths["observations"], paths["scores"], profile=adapter.BLOB_PROFILE
    )
    assert result["schema"] == "langfuse_row_index.v1"
    assert {role: result["sources"][role]["raw_hash"] for role in paths} == {
        role: digest_bytes(path.read_bytes()) for role, path in paths.items()
    }
    assert result["counts"]["scores"] == len(rows["scores"])
    assert result["score_rows"][0]["numeric_field_presence"] == "present"
    assert result["score_rows"][0]["string_field_presence"] == "null"
    assert result["score_rows"][1]["string_field_presence"] == "present"
    assert result["score_rows"][0]["target_observation_index"] == 1
    assert result["score_rows"][2]["association"] == "unresolved_observation_reference"
    assert result["score_rows"][2]["target_observation_index"] is None
    assert result["score_rows"][3]["association"] == "trace_level"
    assert result["score_rows"][3]["target_observation_index"] is None
    assert result["scope"]["selected_file_binding"] == "independent_selected_files"
    assert result["verification"] == []
    assert all("value" not in row and "id" not in row for row in result["score_rows"])

    del rows["scores"][0]["string_value"]
    paths["scores"].write_text(json.dumps(rows["scores"]), encoding="utf-8")
    assert (
        main.main(["audit-langfuse", *argv(paths), "--view", "row-index", "--json"]) == ERROR_EXIT
    )
    streams = capsys.readouterr()
    assert streams.out == "" and canary not in streams.err
    assert json.loads(streams.err)["code"] == "INVALID_INPUT"


def test_row_index_reads_each_selected_file_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = inputs(tmp_path)
    read = adapter.read_local_file
    selected: list[Path] = []

    def traced(path: Path, maximum: int) -> bytes:
        selected.append(path)
        return read(path, maximum)

    monkeypatch.setattr(adapter, "read_local_file", traced)
    adapter.audit_langfuse_row_index_files(
        paths["observations"], paths["scores"], profile=adapter.BLOB_PROFILE
    )
    assert selected == [paths["observations"], paths["scores"]]


def test_row_index_api_projection_fault_is_finite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = inputs(tmp_path)

    def fail(_accounting: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("PRIVATE_AGENT_CANARY")

    monkeypatch.setattr(adapter, "index_langfuse_accounting", fail)
    with pytest.raises(adapter.LangfuseFileError) as caught:
        adapter.audit_langfuse_row_index_files(
            paths["observations"], paths["scores"], profile=adapter.BLOB_PROFILE
        )
    assert caught.value.code == "INTERNAL_ERROR"
    assert caught.value.reason == "LANGFUSE_ROW_INDEX_INVALID"
    assert "PRIVATE_AGENT_CANARY" not in str(caught.value)
