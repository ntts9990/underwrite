"""Exercise the actual client-started stdio process, including its wire bytes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from drivers.goppi_trajectory_verifier_stub import verifier_response
from underwrite_core.canonical import canonical_bytes

from underwrite.agent_surface.server import (
    MAX_REQUEST_BYTES,
    Session,
    _json_text,  # pyright: ignore[reportPrivateUsage]
)
from underwrite.cli import capabilities, record_trajectory
from underwrite.instrument.ingest.deepeval_binding import audit_deepeval_bound_file
from underwrite.service import records as record_service

ROOT = repo_root(Path(__file__).resolve())
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "underwrite-test", "version": "1"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _transcript(*messages: object) -> tuple[list[dict[str, Any]], bytes]:
    encoded = (
        b"\n".join(
            json.dumps(message, separators=(",", ":")).encode()
            if not isinstance(message, bytes)
            else message
            for message in messages
        )
        + b"\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-m", "underwrite.agent_surface"],
        input=encoded,
        capture_output=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return [json.loads(line) for line in result.stdout.splitlines()], result.stderr


def test_capabilities_tool_matches_cli_stdout_and_structure() -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "underwrite_capabilities", "arguments": {}},
        },
    )
    assert stderr == b""
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"
    assert responses[0]["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert [tool["name"] for tool in responses[1]["result"]["tools"]] == [
        "underwrite_capabilities",
        "underwrite_inspect_result_v1",
        "underwrite_inspect_result_v2",
        "underwrite_audit_baseline",
        "underwrite_inspect",
        "underwrite_audit_promptfoo",
        "underwrite_audit_counts",
        "underwrite_audit_deepeval",
        "underwrite_audit_deepeval_trace",
        "underwrite_audit_deepeval_bound",
        "underwrite_audit_langfuse",
        "underwrite_audit_langfuse_score_source",
        "underwrite_inspect_result_v1_actions",
        "underwrite_inspect_result_v2_actions",
        "underwrite_records_read_trajectory",
        "underwrite_review_pair",
        "underwrite_review_artifacts",
        "underwrite_review_artifacts_readiness",
        "underwrite_records_list",
        "underwrite_records_show",
        "underwrite_records_verify",
        "underwrite_records_read_json",
    ]
    cli = subprocess.run(
        [sys.executable, "-I", "-m", "underwrite.cli.main", "capabilities", "--json"],
        capture_output=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[2]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)
    assert result["structuredContent"] == json.loads(cli.stdout)


@pytest.mark.parametrize("mismatched_hash", [False, True])
def test_inspect_tool_preserves_cli_result_including_not_created(
    tmp_path: Path, mismatched_hash: bool
) -> None:
    fixture = ROOT / "fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.case.json"
    case = fixture
    if mismatched_hash:
        case = tmp_path / "case.json"
        manifest = json.loads(fixture.read_bytes())
        manifest["artifact"]["expected_hash"] = "sha256:" + "0" * 64
        case.write_text(json.dumps(manifest), encoding="utf-8")
        artifact = manifest["artifact"]["path"]
        shutil.copyfile(fixture.parent / artifact, tmp_path / artifact)
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_inspect", "arguments": {"case": str(case)}},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [sys.executable, "-I", "-m", "underwrite.cli.main", "inspect", str(case), "--json"],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == (1 if mismatched_hash else 0) and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["projection"] == (
        "not_created" if mismatched_hash else "created"
    )
    assert len(cli.stdout) < 16 * 1024
    assert str(tmp_path) not in text
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments", [{}, {"case": ""}, {"case": "missing.json", "extra": "PRIVATE_CANARY"}]
)
def test_inspect_tool_uses_inspection_error_owner(arguments: dict[str, object]) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_inspect", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == (
        "artifact_inspection_error.v1"
    )
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_promptfoo_tool_matches_summary_cli_and_omits_row_coordinates() -> None:
    source = ROOT / "fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.json"
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_promptfoo", "arguments": {"file": str(source)}},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "audit-promptfoo",
            str(source), "--view", "summary", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["schema"] == "promptfoo_accounting_summary.v1"
    assert result["structuredContent"]["finding_counts"] == {
        "CATEGORY_COUNT_MISMATCH": 1, "SOURCE_FLAGS_DISAGREE": 0
    }
    assert "row_index" not in text and "location" not in text
    assert len(cli.stdout) <= 16 * 1024
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments", [{}, {"file": ""}, {"file": "PRIVATE_CANARY", "extra": "secret"}]
)
def test_promptfoo_tool_uses_evidence_error_owner(arguments: dict[str, object]) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_promptfoo", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_audit_counts_tool_matches_manifest_summary_cli() -> None:
    case = ROOT / "fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.case.json"
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_counts", "arguments": {"case": str(case)}},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "audit-counts",
            str(case), "--view", "summary", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    binding = result["structuredContent"]["source_binding"]
    assert binding["kind"] == "manifest_case"
    assert binding["expected_hash_check"] == "not_supplied"
    assert binding["manifest_hash"].startswith("sha256:")
    assert result["structuredContent"]["detail_state"]["full_view"] == (
        "audit-counts --view full"
    )
    assert "row_index" not in text and "location" not in text
    assert len(cli.stdout) <= 16 * 1024
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments", [{}, {"case": ""}, {"case": "PRIVATE_CANARY", "extra": "secret"}]
)
def test_audit_counts_tool_uses_evidence_error_owner(arguments: dict[str, object]) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_counts", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_deepeval_tool_matches_bounded_accounting_cli_without_source_text() -> None:
    source = ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json"
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_deepeval", "arguments": {"file": str(source)}},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "audit-deepeval",
            str(source), "--view", "summary", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["schema"] == "deepeval_accounting_summary.v1"
    assert result["structuredContent"]["counts"]["metrics"]["error"] == 1
    assert "synthetic deterministic evaluator error" not in text
    assert len(cli.stdout) <= 16 * 1024
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments", [{}, {"file": ""}, {"file": "PRIVATE_CANARY", "extra": "secret"}]
)
def test_deepeval_tool_uses_evidence_error_owner(arguments: dict[str, object]) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_deepeval", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_deepeval_trace_tool_matches_bounded_cli_without_parent_pointers() -> None:
    source = ROOT / "fixtures/examples/deepeval-auto-4.3.1/recovery.json"
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "underwrite_audit_deepeval_trace", "arguments": {"file": str(source)}
            },
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "audit-deepeval",
            str(source), "--view", "trace-summary", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["schema"] == "deepeval_trace_summary.v1"
    assert result["structuredContent"]["counts"]["spans"]["parent_resolved"] > 0
    assert "/testCases/" not in text
    assert len(cli.stdout) <= 16 * 1024
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments", [{}, {"file": ""}, {"file": "PRIVATE_CANARY", "extra": "secret"}]
)
def test_deepeval_trace_tool_uses_evidence_error_owner(arguments: dict[str, object]) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_deepeval_trace", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_deepeval_bound_tool_matches_cli_without_owner_text() -> None:
    source = ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json"
    pack = ROOT / "fixtures/examples/evidence/synthetic-deepeval.bindings.v2.json"
    arguments = {"file": str(source), "bindings": str(pack)}
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_deepeval_bound", "arguments": arguments},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "audit-deepeval",
            str(source), "--bindings", str(pack), "--view", "bound-summary", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    summary = result["structuredContent"]
    assert summary["schema"] == "deepeval_bound_summary.v1"
    assert summary["declaration_source"]["authority"] == "owner_asserted_unverified"
    assert summary["declared_semantic_binding_counts"] == {"bound": 1, "unbound": 2}
    assert "fixed_score" not in text and "synthetic deterministic evaluator error" not in text
    assert len(cli.stdout) <= 16 * 1024
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"file": "x"},
        {"file": "PRIVATE_CANARY", "bindings": "missing"},
        {"file": "x", "bindings": "y", "extra": "PRIVATE_CANARY"},
    ],
)
def test_deepeval_bound_tool_uses_evidence_error_owner(arguments: dict[str, object]) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_deepeval_bound", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


@pytest.mark.parametrize("reader", ["blob", "cli"])
def test_langfuse_tool_matches_bounded_cli_without_rows(reader: str) -> None:
    if reader == "blob":
        folder = ROOT / "fixtures/golden/external/langfuse"
        observations = folder / "observations_v2-2026-09-14T06-40-00.json"
        scores = folder / "scores-2026-09-14T06-40-00.json"
    else:
        folder = ROOT / "fixtures/examples/langfuse-cli"
        observations, scores = folder / "observations.json", folder / "scores.json"
    arguments = {"observations": str(observations), "scores": str(scores)}
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_langfuse", "arguments": arguments},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "audit-langfuse",
            "--observations", str(observations), "--scores", str(scores),
            "--view", "summary", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    summary = result["structuredContent"]
    assert summary["schema"] == "langfuse_accounting_summary.v1"
    assert "observations" not in summary and "scores" not in summary
    assert "reviewer_note" not in text and "synthetic review" not in text
    assert len(cli.stdout) <= 16 * 1024
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"observations": "x"},
        {"observations": "PRIVATE_CANARY", "scores": "missing"},
        {"observations": "x", "scores": "y", "extra": "PRIVATE_CANARY"},
    ],
)
def test_langfuse_tool_uses_evidence_error_owner(arguments: dict[str, object]) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_langfuse", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_langfuse_score_source_tool_matches_existing_cli_summary() -> None:
    folder = ROOT / "fixtures/examples/langfuse-cli"
    observations, scores = folder / "observations.json", folder / "scores.json"
    arguments = {"observations": str(observations), "scores": str(scores)}
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "underwrite_audit_langfuse_score_source", "arguments": arguments
            },
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "audit-langfuse",
            "--observations", str(observations), "--scores", str(scores),
            "--view", "score-source", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    summary = result["structuredContent"]
    assert summary["schema"] == "langfuse_score_source_summary.v1"
    assert summary["counts"]["known"]["API"] > 0
    assert summary["scope"]["score_sources"] == "source_claim"
    assert "synthetic review" not in text
    assert len(cli.stdout) <= 16 * 1024
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"observations": "x"},
        {"observations": "PRIVATE_CANARY", "scores": "missing"},
        {"observations": "x", "scores": "y", "extra": "PRIVATE_CANARY"},
    ],
)
def test_langfuse_score_source_tool_uses_evidence_error_owner(
    arguments: dict[str, object]
) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_audit_langfuse_score_source", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("view", ["summary", "actions"])
def test_inspect_result_summary_tools_match_selected_cli_profile(
    version: str, view: str, tmp_path: Path
) -> None:
    if version == "v1":
        source = ROOT / "fixtures/examples/evidence/common-result-c55.json"
    else:
        source = tmp_path / "PRIVATE_CANARY-result.json"
        result = audit_deepeval_bound_file(
            ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json",
            ROOT / "fixtures/examples/evidence/synthetic-deepeval.bindings.v2.json",
        )
        source.write_bytes(canonical_bytes(result) + b"\n")
    name = "underwrite_inspect_result_" + version
    if view == "actions":
        name += "_actions"
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": {"file": str(source)}},
        },
    )
    assert stderr == b""
    command = [
            sys.executable,
            "-I",
            "-m",
            "underwrite.cli.main",
            "inspect-result",
            str(source),
            "--json",
    ]
    if view == "actions":
        command.extend(("--view", "actions"))
    cli = subprocess.run(
        command,
        capture_output=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["selected_result"]["format"] == (
        "evidence_result." + version
    )
    assert result["structuredContent"]["schema"] == (
        "common_result_inspection_actions.v3"
        if view == "actions"
        else "common_result_inspection.v3"
    )
    assert "PRIVATE_CANARY" not in text


@pytest.mark.parametrize("view", ["summary", "actions"])
@pytest.mark.parametrize("selected_version", ["v1", "v2"])
@pytest.mark.parametrize("malformed", [False, True])
def test_inspect_result_profile_mismatch_is_a_typed_input_error(
    tmp_path: Path, view: str, selected_version: str, malformed: bool
) -> None:
    if selected_version == "v1":
        source = ROOT / "fixtures/examples/evidence/common-result-c55.json"
    else:
        source = tmp_path / "PRIVATE_CANARY-result.json"
        report = audit_deepeval_bound_file(
            ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json",
            ROOT / "fixtures/examples/evidence/synthetic-deepeval.bindings.v2.json",
        )
        source.write_bytes(canonical_bytes(report) + b"\n")
    if malformed:
        document = json.loads(source.read_bytes())
        document["population"] = "PRIVATE_CANARY"
        source = tmp_path / "PRIVATE_CANARY-malformed.json"
        source.write_bytes(canonical_bytes(document) + b"\n")
    requested_version = "v2" if selected_version == "v1" else "v1"
    responses, _ = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": (
                    f"underwrite_inspect_result_{requested_version}_actions"
                    if view == "actions"
                    else f"underwrite_inspect_result_{requested_version}"
                ),
                "arguments": {"file": str(source)},
            },
        },
    )
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    failure = json.loads(result["content"][0]["text"])
    assert (failure["code"], failure["reason"], failure["location"]) == (
        "INVALID_INPUT",
        "UNSUPPORTED_RESULT_SCHEMA",
        "/result/schema",
    )
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_goppi_baseline_tool_matches_cli_without_reflecting_input_path(tmp_path: Path) -> None:
    exchange = Path(
        shutil.copytree(
            ROOT / "fixtures/examples/baseline-event/exchange",
            tmp_path / "PRIVATE_CANARY-exchange",
        )
    )
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "underwrite_audit_baseline",
                "arguments": {"exchange": str(exchange)},
            },
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "underwrite.cli.main",
            "audit-baseline",
            "--exchange",
            str(exchange),
            "--json",
        ],
        capture_output=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["schema"] == "evidence_result.v2"
    assert "PRIVATE_CANARY" not in text


def test_goppi_baseline_tool_refuses_invalid_input_without_reflecting_path() -> None:
    private = "/tmp/UNDERWRITE_PRIVATE_CANARY_MISSING"
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "underwrite_audit_baseline",
                "arguments": {"exchange": private},
            },
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert private not in json.dumps(result)


def test_paired_review_tool_matches_bounded_cli_without_declared_text(tmp_path: Path) -> None:
    examples = ROOT / "fixtures/examples/evidence"
    paths = {
        name: tmp_path / f"PRIVATE_CANARY-{name}.json"
        for name in ("input", "policy", "context")
    }
    for name, source in (
        ("input", "synthetic-paired-complete.json"),
        ("policy", "paired-binary-policy.json"),
        ("context", "synthetic-review-context.json"),
    ):
        shutil.copyfile(examples / source, paths[name])
    context = json.loads(paths["context"].read_text())
    context["conditions"][0]["baseline"] = "IGNORE_THIS_\u202ePRIVATE"
    paths["context"].write_text(json.dumps(context))
    arguments = {name: str(path) for name, path in paths.items()}
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "underwrite_review_pair", "arguments": arguments},
        },
    )
    assert stderr == b""
    cli_args = [sys.executable, "-I", "-m", "underwrite.cli.main", "review-pair"]
    for name, path in paths.items():
        cli_args.extend(("--" + name, str(path)))
    cli_args.extend(("--view", "summary", "--json"))
    cli = subprocess.run(cli_args, capture_output=True, cwd=ROOT, timeout=10, check=False)
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["schema"] == "paired_review_summary.v1"
    assert "PRIVATE_CANARY" not in text and "IGNORE_THIS" not in text
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments",
    [
        {"input": "/tmp/PRIVATE_CANARY_missing", "policy": "policy", "context": "context"},
        {"input": "input", "policy": "policy"},
    ],
)
def test_paired_review_tool_refuses_invalid_input_without_path_leak(
    arguments: dict[str, str]
) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "underwrite_review_pair", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_cross_artifact_review_tool_matches_bounded_cli_and_omits_source_strings(
    tmp_path: Path,
) -> None:
    source = ROOT / "fixtures/conformance/goppi-c55-common"
    assessment = source / "assessment.json"
    result_file = source / "evidence-result.json"
    case_ids = [case["case_id"] for case in json.loads(assessment.read_bytes())["cases"]]
    rows = [
        {
            "success": False,
            "failureReason": 1,
            "testCase": {"metadata": {"case_id": "control-0", "slot_kind": "control"}},
            "prompt": "PRIVATE_CANARY_\u202e",
        },
        *[
            {
                "success": False,
                "failureReason": 1,
                "testCase": {"metadata": {"case_id": case_id, "slot_kind": "subject"}},
                "response": "PRIVATE_CANARY_\u202e",
            }
            for case_id in case_ids
        ],
    ]
    promptfoo = tmp_path / "PRIVATE_CANARY_promptfoo.json"
    promptfoo.write_text(json.dumps({
        "results": {
            "version": 3,
            "results": rows,
            "stats": {"successes": 0, "failures": len(rows), "errors": 0},
        },
        "metadata": {"promptfooVersion": "0.123.0"},
    }))
    arguments = {
        "goppi_assessment": str(assessment),
        "goppi_result": str(result_file),
        "promptfoo": str(promptfoo),
    }
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "underwrite_review_artifacts", "arguments": arguments},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "review-artifacts",
            "--goppi-assessment", str(assessment),
            "--goppi-result", str(result_file),
            "--promptfoo", str(promptfoo),
            "--view", "summary", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    projected = responses[1]["result"]
    assert projected["isError"] is False
    text = projected["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert projected["structuredContent"] == json.loads(text)
    assert projected["structuredContent"]["schema"] == "cross_artifact_review_summary.v1"
    assert len(cli.stdout) <= 16 * 1024
    assert "PRIVATE_CANARY" not in text
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments",
    [
        {"goppi_assessment": "/tmp/PRIVATE_CANARY_missing", "goppi_result": "x", "promptfoo": "y"},
        {"goppi_assessment": "x", "goppi_result": "y"},
        {"goppi_assessment": "x", "goppi_result": "y", "promptfoo": "z", "extra": "q"},
    ],
)
def test_cross_artifact_review_tool_refuses_invalid_input_without_path_leak(
    arguments: dict[str, str],
) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "underwrite_review_artifacts", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_cross_artifact_readiness_tool_matches_cli_without_blocker_coordinates(
    tmp_path: Path,
) -> None:
    source = ROOT / "fixtures/conformance/goppi-c55-common"
    promptfoo = tmp_path / "PRIVATE_CANARY-promptfoo.json"
    golden = ROOT / "fixtures/golden/external/promptfoo-0.124.1/results.json"
    export = json.loads(golden.read_bytes())
    export["results"]["results"][0]["prompt"] = "PRIVATE_CANARY_\u202e"
    promptfoo.write_text(json.dumps(export))
    arguments = {
        "goppi_assessment": str(source / "assessment.json"),
        "goppi_result": str(source / "evidence-result.json"),
        "promptfoo": str(promptfoo),
    }
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_review_artifacts_readiness", "arguments": arguments},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "review-artifacts",
            "--goppi-assessment", arguments["goppi_assessment"],
            "--goppi-result", arguments["goppi_result"],
            "--promptfoo", arguments["promptfoo"],
            "--view", "readiness-summary", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    projected = responses[1]["result"]
    assert projected["isError"] is False
    text = projected["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert projected["structuredContent"] == json.loads(text)
    assert projected["structuredContent"]["schema"] == "cross_artifact_readiness_summary.v1"
    assert len(cli.stdout) <= 16 * 1024
    assert "PRIVATE_CANARY" not in text
    assert "row_index" not in text and "/testCase" not in text
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments",
    [
        {"goppi_assessment": "/tmp/PRIVATE_CANARY_missing", "goppi_result": "x", "promptfoo": "y"},
        {"goppi_assessment": "x", "goppi_result": "y"},
        {
            "goppi_assessment": "x", "goppi_result": "y", "promptfoo": "z",
            "correspondence": "PRIVATE_CANARY",
        },
    ],
)
def test_cross_artifact_readiness_tool_refuses_invalid_input_without_path_leak(
    arguments: dict[str, str],
) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_review_artifacts_readiness", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_records_list_tool_matches_paged_cli_without_private_label(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    examples = ROOT / "fixtures/examples/records"
    (source / "note.txt").write_bytes((examples / "note.txt").read_bytes())
    spec = json.loads((examples / "record-spec.json").read_bytes())
    spec["label"] = "PRIVATE_LABEL_CANARY_\u202e"
    root = tmp_path / "PRIVATE_ROOT_CANARY-store"
    record_service.save_record(root, spec, source)
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_records_list", "arguments": {"root": str(root)}},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "records", "list",
            "--root", str(root), "--view", "page", "--offset", "0", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["schema"] == "record_catalog_page.v1"
    assert result["structuredContent"]["records"][0]["record_id"] == spec["record_id"]
    assert len(cli.stdout) <= 16 * 1024
    assert "PRIVATE_LABEL_CANARY" not in text and "PRIVATE_ROOT_CANARY" not in text
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({}, "USAGE_ERROR"),
        ({"root": "/tmp/PRIVATE_ROOT_CANARY_missing"}, "STORE_NOT_FOUND"),
        ({"root": "x", "offset": "bad"}, "USAGE_ERROR"),
        ({"root": "x", "extra": "PRIVATE_ROOT_CANARY"}, "USAGE_ERROR"),
    ],
)
def test_records_list_tool_uses_record_error_owner_without_path_leak(
    arguments: dict[str, object], code: str
) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_records_list", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    error = json.loads(result["content"][0]["text"])
    assert error["schema"] == "record_error.v1" and error["code"] == code
    assert "PRIVATE_ROOT_CANARY" not in json.dumps(result)


def test_records_show_tool_matches_manifest_summary_without_private_text(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    examples = ROOT / "fixtures/examples/records"
    (source / "note.txt").write_bytes((examples / "note.txt").read_bytes())
    spec = json.loads((examples / "record-spec.json").read_bytes())
    spec["label"] = "PRIVATE_LABEL_CANARY_\u202e"
    spec["files"][0]["source_locator"] = "PRIVATE_SOURCE_CANARY"
    root = tmp_path / "PRIVATE_ROOT_CANARY-store"
    record_service.save_record(root, spec, source)
    arguments = {"root": str(root), "id": spec["record_id"]}
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_records_show", "arguments": arguments},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "records", "show",
            "--root", str(root), "--id", spec["record_id"], "--view", "summary",
            "--file-offset", "0", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["schema"] == "record_view_summary.v1"
    assert result["structuredContent"]["files"][0]["name"] == "source/note.txt"
    assert len(cli.stdout) <= 16 * 1024
    assert not any(
        token in text
        for token in ("PRIVATE_LABEL_CANARY", "PRIVATE_SOURCE_CANARY", "PRIVATE_ROOT_CANARY")
    )
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"root": "/tmp/PRIVATE_ROOT_CANARY_missing", "id": "record.one"},
        {"root": "x", "id": "record.one", "file_offset": "bad"},
        {"root": "x", "id": "record.one", "extra": "PRIVATE_ROOT_CANARY"},
    ],
)
def test_records_show_tool_uses_record_error_owner_without_path_leak(
    arguments: dict[str, object]
) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_records_show", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "record_error.v1"
    assert "PRIVATE_ROOT_CANARY" not in json.dumps(result)


def test_records_verify_tool_matches_cli_and_keeps_invalid_result_as_data(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    examples = ROOT / "fixtures/examples/records"
    (source / "note.txt").write_bytes((examples / "note.txt").read_bytes())
    spec = json.loads((examples / "record-spec.json").read_bytes())
    root = tmp_path / "PRIVATE_ROOT_CANARY-store"
    record_service.save_record(root, spec, source)
    stored = root / "records" / spec["record_id"] / "files" / "source" / "note.txt"
    original = stored.read_bytes()
    stored.write_bytes(b"x" * len(original))
    arguments = {"root": str(root), "id": spec["record_id"]}
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_records_verify", "arguments": arguments},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "records", "verify",
            "--root", str(root), "--id", spec["record_id"], "--view", "summary", "--json",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["schema"] == "record_verification_summary.v1"
    assert result["structuredContent"]["valid"] is False
    assert result["structuredContent"]["file_check_states"] == ["FILE_DIGEST_MISMATCH"]
    assert len(cli.stdout) <= 16 * 1024
    assert "source/note.txt" not in text and "PRIVATE_ROOT_CANARY" not in text


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"root": "/tmp/PRIVATE_ROOT_CANARY_missing", "id": "record.one"},
        {"root": "x", "id": "record.one", "extra": "PRIVATE_ROOT_CANARY"},
    ],
)
def test_records_verify_tool_uses_record_error_owner_without_path_leak(
    arguments: dict[str, object]
) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_records_verify", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "record_error.v1"
    assert "PRIVATE_ROOT_CANARY" not in json.dumps(result)


def test_records_read_json_tool_matches_bounded_cli_and_treats_content_as_data(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.json").write_bytes(
        b'{"next_action":"PRIVATE_CONTENT_CANARY\\u202e\\u2028\\u2029\\udb40\\udc01"}'
    )
    examples = ROOT / "fixtures/examples/records"
    spec = json.loads((examples / "record-spec.json").read_bytes())
    spec["files"][0].update(
        {"name": "source/data.json", "path": "data.json", "media_type": "application/json"}
    )
    root = tmp_path / "PRIVATE_ROOT_CANARY-store"
    record_service.save_record(root, spec, source)
    arguments = {"root": str(root), "id": spec["record_id"], "name": "source/data.json"}
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_records_read_json", "arguments": arguments},
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable, "-I", "-m", "underwrite.cli.main", "records", "read-json",
            "--root", str(root), "--id", spec["record_id"],
            "--name", "source/data.json", "--view", "bounded",
        ],
        capture_output=True, cwd=ROOT, timeout=10, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["content"]["next_action"] == (
        "PRIVATE_CONTENT_CANARY\u202e\u2028\u2029\U000e0001"
    )
    assert "next_action" not in {key for key in result["structuredContent"] if key != "content"}
    assert "PRIVATE_ROOT_CANARY" not in text
    assert len(cli.stdout) <= 16 * 1024
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in text)


def test_records_read_json_tool_refuses_large_content_without_echo(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.json").write_bytes(b'"' + b"PRIVATE_CONTENT_CANARY" * 1000 + b'"')
    examples = ROOT / "fixtures/examples/records"
    spec = json.loads((examples / "record-spec.json").read_bytes())
    spec["files"][0].update(
        {"name": "source/data.json", "path": "data.json", "media_type": "application/json"}
    )
    root = tmp_path / "PRIVATE_ROOT_CANARY-store"
    record_service.save_record(root, spec, source)
    arguments = {"root": str(root), "id": spec["record_id"], "name": "source/data.json"}
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_records_read_json", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert "PRIVATE_CONTENT_CANARY" not in json.dumps(result)
    assert "PRIVATE_ROOT_CANARY" not in json.dumps(result)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"root": "/tmp/PRIVATE_ROOT_CANARY_missing", "id": "record.one", "name": "data.json"},
        {"root": "x", "id": "record.one", "name": "data.json", "extra": "PRIVATE_ROOT_CANARY"},
    ],
)
def test_records_read_json_tool_uses_record_error_owner_without_path_leak(
    arguments: dict[str, object]
) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "underwrite_records_read_json", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "record_error.v1"
    assert "PRIVATE_ROOT_CANARY" not in json.dumps(result)


def test_stored_trajectory_tool_reopens_bytes_without_running_verifier(tmp_path: Path) -> None:
    class CaptureVerifier:
        calls = 0

        def verify(self, snapshot: Path) -> bytes:
            self.calls += 1
            return json.dumps(verifier_response(snapshot)).encode()

    store = tmp_path / "PRIVATE_CANARY-store"
    identifier = "trajectory.smoke-001"
    verifier = CaptureVerifier()
    captured = record_trajectory.run_record_trajectory(
        ROOT / "fixtures/conformance/goppi-trajectory/exchange",
        store,
        identifier,
        verifier=verifier,
    )
    storage = captured["storage"]
    assert isinstance(storage, dict) and storage["state"] == "saved"
    assert verifier.calls == 1
    before = {
        path.relative_to(store): path.read_bytes() for path in store.rglob("*") if path.is_file()
    }
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "underwrite_records_read_trajectory",
                "arguments": {"root": str(store), "id": identifier},
            },
        },
    )
    assert stderr == b""
    cli = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "underwrite.cli.main",
            "records",
            "read-trajectory",
            "--root",
            str(store),
            "--id",
            identifier,
            "--view",
            "summary",
            "--json",
        ],
        capture_output=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )
    assert cli.returncode == 0 and cli.stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text + "\n" == cli.stdout.decode()
    assert result["structuredContent"] == json.loads(text)
    assert result["structuredContent"]["schema"] == "trajectory_record_read_summary.v1"
    assert result["structuredContent"]["native_verification_performed_now"] is False
    assert len(canonical_bytes(responses[1])) < 64 * 1024
    assert "PRIVATE_CANARY" not in text
    after = {
        path.relative_to(store): path.read_bytes() for path in store.rglob("*") if path.is_file()
    }
    assert before == after


@pytest.mark.parametrize(
    "arguments",
    [
        {"root": "/tmp/PRIVATE_CANARY_missing_store", "id": "trajectory.smoke-001"},
        {"root": "/tmp/PRIVATE_CANARY_missing_store", "id": "bad id"},
    ],
)
def test_stored_trajectory_tool_refuses_invalid_input_without_path_leak(
    arguments: dict[str, str]
) -> None:
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "underwrite_records_read_trajectory",
                "arguments": arguments,
            },
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == "evidence_error.v1"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_stored_trajectory_output_limit_is_a_stdio_tool_error(tmp_path: Path) -> None:
    class CaptureVerifier:
        def verify(self, snapshot: Path) -> bytes:
            return json.dumps(verifier_response(snapshot)).encode()

    store = tmp_path / "PRIVATE_CANARY-store"
    identifier = "trajectory.smoke-001"
    record_trajectory.run_record_trajectory(
        ROOT / "fixtures/conformance/goppi-trajectory/exchange",
        store,
        identifier,
        verifier=CaptureVerifier(),
    )
    script = (
        "from underwrite.cli import record_trajectory\n"
        "record_trajectory.SUMMARY_OUTPUT_MAX_BYTES = 1\n"
        "from underwrite.agent_surface.server import main\n"
        "raise SystemExit(main())\n"
    )
    messages: tuple[object, ...] = (
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "underwrite_records_read_trajectory",
                "arguments": {"root": str(store), "id": identifier},
            },
        },
    )
    request = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
    observed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        input=request,
        capture_output=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )
    assert observed.returncode == 0 and observed.stderr == b""
    responses = [json.loads(line) for line in observed.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2]
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_lifecycle_protocol_errors_and_tool_input_refusal() -> None:
    responses, stderr = _transcript(
        b"{broken",
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        INITIALIZE,
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        INITIALIZED,
        {"jsonrpc": "2.0", "id": 4, "method": "no/such/method"},
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "underwrite_missing", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "underwrite_capabilities", "arguments": {"input": "private"}},
        },
    )
    assert stderr == b""
    assert [response.get("id") for response in responses] == [None, 2, 1, 3, 4, 5, 6]
    assert "id" in responses[0] and responses[0]["id"] is None
    assert [responses[index]["error"]["code"] for index in (0, 1, 3, 4, 5)] == [
        -32700,
        -32000,
        -32000,
        -32601,
        -32602,
    ]
    failure = responses[6]["result"]
    assert failure["isError"] is True
    assert json.loads(failure["content"][0]["text"])["code"] == "USAGE_ERROR"
    assert "private" not in json.dumps(failure)


def test_malformed_and_oversize_lines_do_not_change_session_state() -> None:
    responses, stderr = _transcript(
        b'{"jsonrpc":"2.0","id":1,"id":2,"method":"initialize"}',
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":NaN}}',
        b" " * (MAX_REQUEST_BYTES + 1),
        INITIALIZE,
        INITIALIZED,
        {"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
    )
    assert stderr == b""
    assert [response.get("id") for response in responses] == [None, None, None, 1, 7]
    assert all("id" in response and response["id"] is None for response in responses[:3])
    assert [response["error"]["code"] for response in responses[:3]] == [
        -32700,
        -32700,
        -32600,
    ]
    assert responses[3]["result"]["protocolVersion"] == "2025-11-25"
    assert responses[4]["result"]["tools"][0]["name"] == "underwrite_capabilities"


def test_unsupported_revision_negotiates_the_supported_version() -> None:
    unsupported = json.loads(json.dumps(INITIALIZE))
    unsupported["params"]["protocolVersion"] = "2099-01-01"
    responses, _ = _transcript(unsupported)
    assert [response["id"] for response in responses] == [1]
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"


def test_ping_works_during_initialization_and_operation() -> None:
    responses, _ = _transcript(
        INITIALIZE,
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        INITIALIZED,
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    )
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert responses[1]["result"] == responses[2]["result"] == {}


def test_bad_initialized_notification_does_not_enter_operation() -> None:
    responses, _ = _transcript(
        INITIALIZE,
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": []},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        INITIALIZED,
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    )
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert [responses[1]["error"]["code"]] == [-32000]
    assert responses[2]["result"]["tools"][0]["name"] == "underwrite_capabilities"


def test_initialize_requires_client_name_and_version() -> None:
    incomplete = json.loads(json.dumps(INITIALIZE))
    incomplete["params"]["clientInfo"] = {}
    incomplete["id"] = 2
    responses, _ = _transcript(incomplete, INITIALIZE)
    assert [response["id"] for response in responses] == [2, 1]
    assert [responses[0]["error"]["code"]] == [-32602]
    assert responses[1]["result"]["serverInfo"]["name"] == "underwrite"


def test_non_object_call_arguments_are_protocol_invalid() -> None:
    responses, _ = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "underwrite_capabilities", "arguments": ["private"]},
        },
    )
    assert [response["id"] for response in responses] == [1, 2]
    assert [responses[1]["error"]["code"]] == [-32602]
    assert "private" not in json.dumps(responses)


def test_standard_meta_is_accepted_and_unissued_cursor_is_refused() -> None:
    bad_icons = json.loads(json.dumps(INITIALIZE))
    bad_icons["id"] = 2
    bad_icons["params"]["clientInfo"]["icons"] = "invalid"
    bad_roots = json.loads(json.dumps(INITIALIZE))
    bad_roots["id"] = 3
    bad_roots["params"]["capabilities"]["roots"] = "invalid"
    responses, _ = _transcript(
        bad_icons,
        bad_roots,
        INITIALIZE,
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {"_meta": []}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        INITIALIZED,
        {"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {"_meta": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {"_meta": {}}},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {"cursor": "none"}},
    )
    assert [response["id"] for response in responses] == [2, 3, 1, 4, 5, 6, 7]
    assert [responses[index]["error"]["code"] for index in (0, 1, 3, 6)] == [
        -32602,
        -32602,
        -32000,
        -32602,
    ]
    assert responses[4]["result"] == {}
    assert responses[5]["result"]["tools"][0]["name"] == "underwrite_capabilities"


def test_malformed_nested_capability_is_rejected() -> None:
    malformed = json.loads(json.dumps(INITIALIZE))
    malformed["id"] = 2
    malformed["params"]["capabilities"]["sampling"] = {"context": "invalid"}
    responses, _ = _transcript(malformed, INITIALIZE)
    assert [response["id"] for response in responses] == [2, 1]
    assert [responses[0]["error"]["code"]] == [-32602]
    assert responses[1]["result"]["protocolVersion"] == "2025-11-25"


def test_exponent_overflow_is_a_parse_error() -> None:
    responses, _ = _transcript(
        b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"_meta":{"progressToken":1e999}}}'
    )
    assert [response["error"]["code"] for response in responses] == [-32700]
    assert "id" in responses[0] and responses[0]["id"] is None


def test_task_and_progress_fields_follow_negotiated_capabilities() -> None:
    malformed_tasks = json.loads(json.dumps(INITIALIZE))
    malformed_tasks["id"] = 2
    malformed_tasks["params"]["capabilities"]["tasks"] = {
        "requests": {"sampling": {"createMessage": "invalid"}}
    }
    responses, _ = _transcript(
        malformed_tasks,
        INITIALIZE,
        INITIALIZED,
        {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {"_meta": {"progressToken": []}}},
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "underwrite_capabilities", "arguments": {}, "task": {}},
        },
        {"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {"_meta": {"progressToken": "ok"}}},
    )
    assert [response["id"] for response in responses] == [2, 1, 3, 4, 5]
    assert [responses[index]["error"]["code"] for index in (0, 2, 3)] == [
        -32602,
        -32602,
        -32602,
    ]
    assert responses[4]["result"] == {}


def test_internal_failure_is_fixed_and_logs_no_exception_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failure(argv: list[str]) -> int:
        raise RuntimeError("PRIVATE INPUT")

    monkeypatch.setattr(capabilities, "main", failure)
    session = Session()
    session.phase = "ready"
    response = session.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "underwrite_capabilities", "arguments": {}},
        }
    )
    assert response is not None
    assert response["error"] == {"code": -32603, "message": "Internal error"}
    diagnostic = capsys.readouterr().err
    assert diagnostic == "underwrite-mcp internal error: RuntimeError\n"
    assert "PRIVATE" not in diagnostic


def test_raw_format_control_in_cli_json_text_is_refused() -> None:
    with pytest.raises(ValueError, match="^UNSAFE_JSON_TEXT$"):
        _json_text('{"name":"\u202e"}\n')


def test_cli_output_limit_remains_a_stdio_tool_error_without_partial_result() -> None:
    script = (
        "from underwrite.cli import capabilities\n"
        "from underwrite.cli.evidence import run_cli\n"
        "def bounded(argv):\n"
        "    return run_cli('capabilities', argv, capabilities.configure, "
        "capabilities._produce, 'capabilities.v1', output_max_bytes=1)\n"
        "capabilities.main = bounded\n"
        "from underwrite.agent_surface.server import main\n"
        "raise SystemExit(main())\n"
    )
    messages: tuple[object, ...] = (
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "underwrite_capabilities", "arguments": {}},
        },
    )
    request = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
    observed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        input=request,
        capture_output=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )
    assert observed.returncode == 0 and observed.stderr == b""
    responses = [json.loads(line) for line in observed.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2]
    result = responses[1]["result"]
    assert result["isError"] is True
    assert "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["code"] == "OUTPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize("view", ["summary", "actions"])
def test_inspect_result_output_limit_is_a_stdio_tool_error_without_partial_result(
    view: str,
) -> None:
    script = (
        "from underwrite.cli import inspect_result\n"
        "from underwrite.cli.evidence import ReportOutput as OriginalReportOutput\n"
        "def bounded_report(*args, **kwargs):\n"
        "    kwargs['max_bytes'] = 1\n"
        "    return OriginalReportOutput(*args, **kwargs)\n"
        "inspect_result.ReportOutput = bounded_report\n"
        "from underwrite.agent_surface.server import main\n"
        "raise SystemExit(main())\n"
    )
    messages: tuple[object, ...] = (
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": (
                    "underwrite_inspect_result_v1_actions"
                    if view == "actions"
                    else "underwrite_inspect_result_v1"
                ),
                "arguments": {
                    "file": str(ROOT / "fixtures/examples/evidence/common-result-c55.json")
                },
            },
        },
    )
    request = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
    observed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        input=request,
        capture_output=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )
    assert observed.returncode == 0 and observed.stderr == b""
    responses = [json.loads(line) for line in observed.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2]
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["code"] == "OUTPUT_LIMIT_EXCEEDED"


def test_goppi_baseline_output_limit_is_a_stdio_tool_error_without_partial_result() -> None:
    script = (
        "from underwrite.cli import audit_baseline\n"
        "from underwrite.cli.evidence import run_cli as original_run_cli\n"
        "def bounded(command, argv, configure, produce, result_schema):\n"
        "    return original_run_cli(command, argv, configure, produce, result_schema, "
        "output_max_bytes=1)\n"
        "audit_baseline.run_cli = bounded\n"
        "from underwrite.agent_surface.server import main\n"
        "raise SystemExit(main())\n"
    )
    messages: tuple[object, ...] = (
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "underwrite_audit_baseline",
                "arguments": {
                    "exchange": str(ROOT / "fixtures/examples/baseline-event/exchange")
                },
            },
        },
    )
    request = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
    observed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        input=request,
        capture_output=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )
    assert observed.returncode == 0 and observed.stderr == b""
    responses = [json.loads(line) for line in observed.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2]
    result = responses[1]["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["code"] == "OUTPUT_LIMIT_EXCEEDED"
