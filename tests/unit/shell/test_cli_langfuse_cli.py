"""Pinned Langfuse CLI files reach one offline accounting and row-index seam."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import main
from underwrite.instrument.ingest import langfuse_cli as adapter
from underwrite.instrument.ingest.langfuse_accounting import LangfuseFileError
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

ROOT = repo_root(Path(__file__).resolve())
FIXTURES = ROOT / "fixtures/examples/langfuse-cli"
GOLDEN = ROOT / "fixtures/golden/external/langfuse"
OBSERVATION_COUNT = 4
SCORE_COUNT = 5
TRUNCATED_SCORE_COUNT = 3
ERROR_EXIT = 2


def _args(observations: Path, scores: Path, *, view: str = "accounting") -> list[str]:
    argv = [
        "audit-langfuse",
        "--observations",
        str(observations),
        "--scores",
        str(scores),
        "--profile",
        "cli-1.2.4",
    ]
    if view != "accounting":
        argv.extend(("--view", view))
    return [*argv, "--json"]


def _invoke(
    capsys: pytest.CaptureFixture[str],
    observations: Path,
    scores: Path,
    *,
    view: str = "accounting",
) -> tuple[int, dict[str, Any], str, str]:
    code = main.main(_args(observations, scores, view=view))
    streams = capsys.readouterr()
    payload = json.loads(streams.out if code == 0 else streams.err)
    return code, payload, streams.out, streams.err


def test_exact_cli_envelopes_produce_typed_accounting_and_minimized_view(
    capsys: pytest.CaptureFixture[str],
) -> None:
    observations, scores = FIXTURES / "observations.json", FIXTURES / "scores.json"
    code, report, stdout, stderr = _invoke(capsys, observations, scores)
    assert code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report == adapter.audit_langfuse_cli_bytes(
        observations.read_bytes(), scores.read_bytes()
    )
    assert report["schema"] == "langfuse_cli_accounting.v1"
    assert report["counts"]["observations"] == OBSERVATION_COUNT
    assert report["counts"]["scores"] == SCORE_COUNT
    assert report["counts"]["by_data_type"] == dict.fromkeys(
        ("NUMERIC", "BOOLEAN", "CATEGORICAL", "TEXT", "CORRECTION"), 1
    )
    assert report["scores"][1]["value"] is False
    assert report["retrieval"]["scores"]["pagination"]["truncated"] is False
    for role, path in (("observations", observations), ("scores", scores)):
        assert report["sources"][role]["raw_hash"] == digest_bytes(path.read_bytes())
        assert report["sources"][role]["claims_verified"] is False
    packaged_validator("langfuse_cli_accounting.v1").validate(report)  # pyright: ignore[reportUnknownMemberType]

    code, index, stdout, stderr = _invoke(capsys, observations, scores, view="row-index")
    assert code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(index) + b"\n"
    assert index == adapter.audit_langfuse_cli_bytes(
        observations.read_bytes(), scores.read_bytes(), view="row-index"
    )
    assert index["schema"] == "langfuse_cli_row_index.v1"
    assert len(index["score_rows"]) == SCORE_COUNT
    assert set(index["score_rows"][0]) == {
        "score_index",
        "data_type",
        "subject_availability",
        "subject_kind",
        "association",
        "target_observation_index",
    }
    assert not {"scores", "observations"} & index.keys()
    assert "quality" not in stdout and "score-1" not in stdout
    assert "value" not in index["score_rows"][0]
    packaged_validator("langfuse_cli_row_index.v1").validate(index)  # pyright: ignore[reportUnknownMemberType]


def test_native_body_and_truncated_capture_report_their_actual_scope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    observations = FIXTURES / "observations-body.json"
    scores = FIXTURES / "scores-body.json"
    code, report, _, stderr = _invoke(capsys, observations, scores)
    assert code == 0 and stderr == ""
    assert report["retrieval"]["observations"]["capture_kind"] == "output_body"
    assert report["retrieval"]["scores"]["http_status"] is None
    assert report["counts"]["scores"] == SCORE_COUNT

    partial = FIXTURES / "scores-truncated.json"
    code, report, _, stderr = _invoke(capsys, FIXTURES / "observations.json", partial)
    assert code == 0 and stderr == ""
    assert report["counts"]["scores"] == TRUNCATED_SCORE_COUNT
    assert report["retrieval"]["scores"]["pagination"]["truncated"] is True
    assert report["planned"]["missing_execution_count"] is None


@pytest.mark.parametrize("view", ["accounting", "row-index"])
def test_human_output_names_partial_retrieval_and_inventory_scope_without_source_content(
    capsys: pytest.CaptureFixture[str], view: str
) -> None:
    argv = _args(FIXTURES / "observations.json", FIXTURES / "scores-truncated.json", view=view)
    assert main.main(argv[:-1]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    assert "truncated True" in streams.out
    assert "export completeness and planned executions are unknown" in streams.out
    assert "applies only when missing-execution accounting is needed" in streams.out
    assert "This command cannot ingest it" in streams.out
    assert "Descriptive accounting needs no additional collection" in streams.out
    assert "synthetic review" not in streams.out
    assert "content-length" not in streams.out


@pytest.mark.parametrize("view", ["accounting", "row-index"])
@pytest.mark.parametrize("score_index", [3, 4])
def test_non_nfc_selected_score_text_is_a_finite_refusal_in_both_views(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], view: str, score_index: int
) -> None:
    source = json.loads((FIXTURES / "scores.json").read_bytes())
    source["body"]["data"][score_index]["value"] = "private-e\u0301"
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    code, error, stdout, stderr = _invoke(capsys, FIXTURES / "observations.json", scores, view=view)
    assert code == ERROR_EXIT and stdout == ""
    assert (error["code"], error["reason"], error["location"]) == (
        "UNSUPPORTED_PROFILE",
        "LANGFUSE_CLI_NONCANONICAL_SCORE_VALUE",
        f"/scores/{score_index}/value",
    )
    assert "private" not in stderr


def test_file_api_reads_each_role_once_and_matches_bytes_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observations, scores = FIXTURES / "observations.json", FIXTURES / "scores.json"
    original = adapter.read_langfuse_file
    reads: list[str] = []

    def read(path: Path, role: str) -> bytes:
        reads.append(role)
        return original(path, role)

    monkeypatch.setattr(adapter, "read_langfuse_file", read)
    report = adapter.audit_langfuse_cli_files(observations, scores)
    assert report == adapter.audit_langfuse_cli_bytes(
        observations.read_bytes(), scores.read_bytes()
    )
    assert reads == ["observations", "scores"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("role", ["observations", "scores"])
def test_invalid_utf8_is_typed_private_and_atomic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], role: str
) -> None:
    private = tmp_path / "PRIVATE_SENTINEL.json"
    private.write_bytes(b"\xffPRIVATE_SENTINEL")
    paths = {
        "observations": FIXTURES / "observations.json",
        "scores": FIXTURES / "scores.json",
    }
    paths[role] = private
    code, error, stdout, stderr = _invoke(capsys, paths["observations"], paths["scores"])
    assert code == ERROR_EXIT and stdout == ""
    assert error["schema"] == "evidence_error.v1"
    assert (error["code"], error["reason"], error["location"]) == (
        "INVALID_INPUT",
        "INVALID_JSON_INPUT",
        "/" + role,
    )
    assert "PRIVATE_SENTINEL" not in stderr


def test_missing_packaged_contract_precedes_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(name: str) -> Any:
        del name
        raise SchemaConfigurationError("private source path")

    def forbidden(path: Path, role: str) -> bytes:
        pytest.fail("read started before packaged contracts loaded")

    monkeypatch.setattr(adapter, "packaged_validator", missing)
    monkeypatch.setattr(adapter, "read_langfuse_file", forbidden)
    with pytest.raises(LangfuseFileError) as caught:
        adapter.audit_langfuse_cli_files(tmp_path / "missing", tmp_path / "missing")
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "SCHEMA_CONFIGURATION_ERROR",
        "INVALID_PACKAGED_CONTRACT",
        "",
    )


def test_capabilities_and_explicit_blob_default_route_are_stable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main.main(["capabilities", "--json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    command = next(row for row in catalog["commands"] if row["command"] == "audit-langfuse")
    profiles = {item["name"]: item for item in command["profiles"]}
    assert profiles["cli-1.2.4"]["required_inputs"] == ["observations", "scores", "profile"]
    assert profiles["cli-1.2.4"]["output_schemas"] == ["langfuse_cli_accounting.v1"]
    assert profiles["cli-1.2.4|row-index"]["output_schemas"] == ["langfuse_cli_row_index.v1"]

    argv = [
        "--observations",
        str(GOLDEN / "observations_v2-2026-09-14T06-40-00.json"),
        "--scores",
        str(GOLDEN / "scores-2026-09-14T06-40-00.json"),
        "--json",
    ]
    assert main.main(["audit-langfuse", *argv]) == 0
    default = capsys.readouterr().out
    assert main.main(["audit-langfuse", *argv, "--profile", "blob-4.35.0"]) == 0
    assert capsys.readouterr().out == default
    assert json.loads(default)["schema"] == "langfuse_accounting.v1"
