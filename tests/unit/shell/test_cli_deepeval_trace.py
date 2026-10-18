"""Explicit DeepEval trace-index route preserves native source boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import audit_deepeval
from underwrite.instrument.evidence.deepeval_accounting import MAX_CASES
from underwrite.instrument.evidence.deepeval_trace import MAX_SPANS
from underwrite.instrument.ingest import deepeval_trace as adapter
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES, DEFAULT_MAX_DEPTH
from underwrite.instrument.ingest.deepeval_accounting import DeepevalFileError
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

ROOT = repo_root(Path(__file__).resolve())
FIXTURES = ROOT / "fixtures/golden/external/deepeval-4.2.8/traces"
SPAN_COUNT = 3
RESOLVED_PARENT_COUNT = 2
ERROR_EXIT = 2
OVERSIZED_SPAN_COUNT = 1025


def _invoke(
    capsys: pytest.CaptureFixture[str], path: Path, *options: str
) -> tuple[int, dict[str, Any], str, str]:
    exit_code = audit_deepeval.main([str(path), *options, "--json"])
    streams = capsys.readouterr()
    return exit_code, json.loads(streams.out or streams.err), streams.out, streams.err


@pytest.mark.parametrize("name", ["success", "recovery"])
def test_native_trace_has_explicit_minimized_machine_route(
    capsys: pytest.CaptureFixture[str], name: str
) -> None:
    path = FIXTURES / f"{name}.json"
    exit_code, report, stdout, stderr = _invoke(
        capsys, path, "--version", "4.2.8", "--view", "trace-index"
    )
    assert exit_code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["schema"] == "deepeval_trace_index.v1"
    assert report["source"] == {
        "format": "deepeval.test-run",
        "format_version": "4.2.8",
        "claims_verified": False,
    }
    assert report["raw_hash"] == digest_bytes(path.read_bytes())
    assert report == adapter.audit_deepeval_trace_bytes(path.read_bytes(), version="4.2.8")
    assert report["counts"]["cases"]["total"] == 1
    assert report["counts"]["spans"]["total"] == SPAN_COUNT
    assert report["cases"][0]["source_success"] is True
    assert report["cases"][0]["trace_status"] == "SUCCESS"
    assert report["counts"]["spans"]["status_errored"] == (1 if name == "recovery" else 0)
    assert report["counts"]["spans"]["parent_resolved"] == RESOLVED_PARENT_COUNT
    packaged_validator("deepeval_trace_index.v1").validate(report)  # pyright: ignore[reportUnknownMemberType]
    source = json.loads(path.read_bytes())
    trace = source["testCases"][0]["trace"]
    assert trace["uuid"] not in stdout
    assert trace["agentSpans"][0]["uuid"] not in stdout
    for sentinel in ("primary_lookup", "fallback_lookup", "synthetic-arithmetic-case"):
        assert sentinel not in stdout


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        (("--version", "4.1.1", "--view", "trace-index"), "DEEPEVAL_TRACE_VERSION_UNSUPPORTED"),
        (
            ("--version", "4.2.8", "--view", "trace-index", "--bindings", "missing"),
            "DEEPEVAL_TRACE_BINDINGS_UNSUPPORTED",
        ),
    ],
)
def test_invalid_route_refused_before_source_read(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: tuple[str, ...],
    reason: str,
) -> None:
    path = tmp_path / "PRIVATE_SENTINEL.json"
    exit_code, error, stdout, stderr = _invoke(capsys, path, *options)
    assert exit_code == ERROR_EXIT and stdout == ""
    assert error["schema"] == "evidence_error.v1"
    assert error["code"] == "UNSUPPORTED_FORMAT"
    assert error["reason"] == reason
    assert "PRIVATE_SENTINEL" not in stderr


def test_native_recovery_human_output_is_status_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    success = FIXTURES / "success.json"
    assert audit_deepeval.main([str(success), "--version", "4.2.8", "--view", "trace-index"]) == 0
    success_streams = capsys.readouterr()
    assert success_streams.err == ""
    assert "Next:" not in success_streams.out

    path = FIXTURES / "recovery.json"
    assert audit_deepeval.main([str(path), "--version", "4.2.8", "--view", "trace-index"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    assert "ERRORED" in streams.out
    assert "source" in streams.out.lower()
    assert "unknown" in streams.out.lower()
    assert "Next: INSPECT_ERRORED_STEP_CONTEXT" in streams.out
    assert "primary_lookup" not in streams.out
    assert "fallback_lookup" not in streams.out


def test_explicit_accounting_view_preserves_default_bytes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = ROOT / "fixtures/golden/external/deepeval-4.2.8/ordinary.json"
    args = [str(path), "--version", "4.2.8", "--json"]
    assert audit_deepeval.main(args) == 0
    default = capsys.readouterr()
    assert audit_deepeval.main([*args, "--view", "accounting"]) == 0
    explicit = capsys.readouterr()
    assert default.out == explicit.out and default.err == explicit.err


def test_file_api_reads_source_once_and_matches_bytes_api(monkeypatch: pytest.MonkeyPatch) -> None:
    path = FIXTURES / "recovery.json"
    original = adapter.read_deepeval_file
    reads: list[Path] = []

    def counted(source: Path) -> bytes:
        reads.append(source)
        return original(source)

    monkeypatch.setattr(adapter, "read_deepeval_file", counted)
    assert adapter.audit_deepeval_trace_file(path, version="4.2.8") == (
        adapter.audit_deepeval_trace_bytes(path.read_bytes(), version="4.2.8")
    )
    assert reads == [path]


@pytest.mark.parametrize("raw", [b' {"testCases":[],"testCases":[]}', b"\xffPRIVATE_SENTINEL"])
def test_strict_json_refusal_has_empty_stdout_and_no_source_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes
) -> None:
    path = tmp_path / "PRIVATE_SENTINEL.json"
    path.write_bytes(raw)
    exit_code, error, stdout, stderr = _invoke(
        capsys, path, "--version", "4.2.8", "--view", "trace-index"
    )
    assert exit_code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INVALID_INPUT"
    assert error["location"] == "/file"
    assert "PRIVATE_SENTINEL" not in stderr


def test_decoder_depth_limit_preserves_shared_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "PRIVATE_SENTINEL.json"
    path.write_bytes(b"[" * (DEFAULT_MAX_DEPTH + 1) + b"0" + b"]" * (DEFAULT_MAX_DEPTH + 1))
    exit_code, error, stdout, stderr = _invoke(
        capsys, path, "--version", "4.2.8", "--view", "trace-index"
    )
    assert exit_code == ERROR_EXIT and stdout == ""
    assert (error["code"], error["reason"], error["location"]) == (
        "INPUT_LIMIT_EXCEEDED",
        "DEPTH_LIMIT_EXCEEDED",
        "/file",
    )
    assert "PRIVATE_SENTINEL" not in stderr


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_source_is_bounded_and_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    path = tmp_path / "PRIVATE_SENTINEL"
    if kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    exit_code, error, stdout, stderr = _invoke(
        capsys, path, "--version", "4.2.8", "--view", "trace-index"
    )
    assert exit_code == ERROR_EXIT and stdout == ""
    assert error["code"] == "SOURCE_READ_FAILED"
    assert "PRIVATE_SENTINEL" not in stderr


def test_packaged_contract_is_required_before_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(_: str) -> Any:
        raise SchemaConfigurationError("PRIVATE_SENTINEL")

    def forbidden(_: Path) -> bytes:
        pytest.fail("source read before selected contract")

    monkeypatch.setattr(adapter, "packaged_validator", missing)
    monkeypatch.setattr(adapter, "read_deepeval_file", forbidden)
    with pytest.raises(DeepevalFileError) as caught:
        adapter.audit_deepeval_trace_file(tmp_path / "PRIVATE_SENTINEL", version="4.2.8")
    assert (caught.value.code, caught.value.reason) == (
        "SCHEMA_CONFIGURATION_ERROR",
        "INVALID_PACKAGED_CONTRACT",
    )


def test_selected_private_enum_refuses_without_leaking_source_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = json.loads((FIXTURES / "recovery.json").read_bytes())
    source["testCases"][0]["trace"]["toolSpans"][0]["status"] = "PRIVATE_SENTINEL"
    path = tmp_path / "PRIVATE_SENTINEL.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    exit_code, error, stdout, stderr = _invoke(
        capsys, path, "--version", "4.2.8", "--view", "trace-index"
    )
    assert exit_code == ERROR_EXIT and stdout == ""
    assert error["schema"] == "evidence_error.v1"
    assert "PRIVATE_SENTINEL" not in stderr


def test_real_cli_enforces_file_byte_and_global_span_caps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b" " * (DEFAULT_MAX_BYTES + 1))
    exit_code, error, stdout, _ = _invoke(
        capsys, path, "--version", "4.2.8", "--view", "trace-index"
    )
    assert exit_code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INPUT_LIMIT_EXCEEDED"

    source = {
        "testCases": [
            {
                "trace": {
                    "uuid": "trace",
                    "toolSpans": [
                        {"uuid": f"span-{index}", "type": "tool", "status": "SUCCESS"}
                        for index in range(OVERSIZED_SPAN_COUNT)
                    ],
                }
            }
        ]
    }
    path.write_text(json.dumps(source), encoding="utf-8")
    exit_code, error, stdout, _ = _invoke(
        capsys, path, "--version", "4.2.8", "--view", "trace-index"
    )
    assert exit_code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INPUT_LIMIT_EXCEEDED"


def test_real_cli_emits_maximal_accepted_report_with_newline_and_no_source_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "PRIVATE_SENTINEL"
    base_spans, remainder = divmod(MAX_SPANS, MAX_CASES)
    cases: list[dict[str, object]] = []
    for index in range(MAX_CASES):
        span_count = base_spans + (index < remainder)
        agent_id = f"{canary}-agent-{index}"
        cases.append(
            {
                "success": True,
                "name": canary,
                "trace": {
                    "uuid": f"{canary}-trace-{index}",
                    "status": "SUCCESS",
                    "input": canary,
                    "agentSpans": (
                        [
                            {
                                "uuid": agent_id,
                                "type": "agent",
                                "status": "ERRORED" if index == 0 else "SUCCESS",
                                "output": canary,
                            }
                        ]
                        if span_count
                        else []
                    ),
                    "toolSpans": [
                        {
                            "uuid": f"{canary}-tool-{index}-{tool_index}",
                            "parentUuid": agent_id,
                            "type": "tool",
                            "status": "SUCCESS",
                            "error": canary,
                        }
                        for tool_index in range(max(0, span_count - 1))
                    ],
                },
            }
        )
    source = {"testCases": cases}
    path = tmp_path / "PRIVATE_SENTINEL.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    exit_code, report, stdout, stderr = _invoke(
        capsys, path, "--version", "4.2.8", "--view", "trace-index"
    )
    assert exit_code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert len(stdout.encode()) <= 1024 * 1024
    assert report["counts"]["cases"]["total"] == MAX_CASES
    assert report["counts"]["spans"]["total"] == MAX_SPANS
    assert report["counts"]["spans"]["status_errored"] == 1
    assert canary not in stdout
