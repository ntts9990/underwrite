"""One-file metric accounting reuses ingestion and preserves evidence errors."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from underwrite_core.canonical import canonical_bytes, content_digest, digest_bytes

from underwrite.cli import audit_deepeval
from underwrite.cli.evidence import ReportOutput, run_cli
from underwrite.instrument.ingest import deepeval_accounting as adapter
from underwrite.instrument.ingest import transport
from underwrite.instrument.ingest.application import packaged_ingestor
from underwrite.instrument.ingest.project import Source

ERROR_EXIT = 2


def payload() -> dict[str, Any]:
    def metric(value: float) -> dict[str, object]:
        return {"name": "Exact Match", "threshold": 0.5, "success": value > 0, "score": value}

    return {
        "testFile": "/private/test-file.py",
        "testPassed": 1,
        "testFailed": 2,
        "testCases": [
            {
                "name": "pass",
                "input": "private prompt",
                "order": 0,
                "success": True,
                "metricsData": [metric(1.0)],
            },
            {
                "name": "fail",
                "input": "private prompt",
                "order": 1,
                "success": False,
                "metricsData": [metric(0.0)],
            },
            {
                "name": "mixed",
                "input": "private prompt",
                "order": 2,
                "success": False,
                "metricsData": [
                    metric(1.0),
                    {
                        "name": "Always Errors",
                        "threshold": 0.5,
                        "success": False,
                        "error": "synthetic evaluator error",
                    },
                ],
            },
        ],
    }


def source_file(tmp_path: Path, value: object | None = None) -> Path:
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload() if value is None else value), encoding="utf-8")
    return path


def invoke(path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any], str, str]:
    code = audit_deepeval.main([str(path), "--version", "4.1.1", "--json"])
    streams = capsys.readouterr()
    return code, json.loads(streams.out if code == 0 else streams.err), streams.out, streams.err


def test_one_file_reports_mixed_metrics_and_bound_identities(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = source_file(tmp_path)
    raw = path.read_bytes()
    code, report, stdout, stderr = invoke(path, capsys)
    assert code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["source"] == {
        "format": adapter.SOURCE[0],
        "format_version": adapter.SOURCE[1],
        "claims_verified": False,
    }
    assert report["raw_hash"] == digest_bytes(raw)
    observed = (
        packaged_ingestor(max_bytes=len(raw), max_depth=64)
        .file(path, Source(*adapter.SOURCE, None))
        .observation
    )
    assert report["observation_digest"] == content_digest(observed)
    assert report["counts"]["cases"] == {
        "total": 3,
        "source_true": 1,
        "source_false": 2,
        "source_success_absent": 0,
    }
    assert report["counts"]["metrics"] == {
        "total": 4,
        "scored": 3,
        "error": 1,
        "unscored": 0,
        "observed_zero": 1,
        "scored_source_true": 2,
        "scored_source_false": 1,
    }
    mixed = report["cases"][-1]["metrics"]
    assert mixed[0]["state"] == "scored" and mixed[0]["score"] == 1
    assert mixed[1]["state"] == "error" and "score" not in mixed[1]
    assert report["declared_case_counts"]["comparison"] == {
        "passed": "matches",
        "failed": "matches",
    }
    assert "/private/test-file.py" not in stdout and "private prompt" not in stdout
    assert path.read_bytes() == raw


def test_source_descriptor_is_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = source_file(tmp_path)
    original = transport.read_descriptor
    reads: list[int] = []

    def track(descriptor: int, maximum: int) -> bytes:
        reads.append(descriptor)
        return original(descriptor, maximum)

    monkeypatch.setattr(transport, "read_descriptor", track)
    adapter.audit_deepeval_file(path)
    assert len(reads) == 1


@pytest.mark.parametrize("raw", [b'{"testCases":[],"testCases":[]}', b"\xff", b'{"testCases": ['])
def test_invalid_source_json_uses_existing_error_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes
) -> None:
    path = tmp_path / "secret-name.json"
    path.write_bytes(raw)
    code, error, stdout, stderr = invoke(path, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["schema"] == "evidence_error.v1" and error["code"] == "INVALID_INPUT"
    assert error["location"] == "/file" and str(path) not in stderr


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_nonregular_source_is_a_typed_nonblocking_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    path = tmp_path / "source"
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    code, error, stdout, _ = invoke(path, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "SOURCE_READ_FAILED"


@pytest.mark.parametrize("axis", ["bytes", "depth"])
def test_ingestion_resource_limits_remain_typed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, axis: str
) -> None:
    path = source_file(tmp_path)
    monkeypatch.setattr(adapter, "DEFAULT_MAX_BYTES" if axis == "bytes" else "DEFAULT_MAX_DEPTH", 1)
    code, error, stdout, _ = invoke(path, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize("value", [None, True, "1"])
def test_raw_metric_score_type_is_checked_before_common_normalization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], value: object
) -> None:
    body = payload()
    body["testCases"][0]["metricsData"][0]["score"] = value
    code, error, stdout, _ = invoke(source_file(tmp_path, body), capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["reason"] == "MALFORMED_DEEPEVAL_PAYLOAD"


def test_new_instrument_numeric_diagnostic_maps_to_existing_evidence_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = payload()
    body["runDuration"] = 2**54
    code, error, stdout, _ = invoke(source_file(tmp_path, body), capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INVALID_INPUT"
    assert error["reason"] == "INT_OUT_OF_SAFE_RANGE"


def test_nonempty_conversational_scope_is_not_reported_as_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = payload()
    body["conversationalTestCases"] = [{}]
    code, error, stdout, _ = invoke(source_file(tmp_path, body), capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "UNSUPPORTED_PROFILE"


def test_source_count_disagreement_is_a_successful_descriptive_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = payload()
    body["testFailed"] = 0
    code, report, _, stderr = invoke(source_file(tmp_path, body), capsys)
    assert code == 0 and stderr == ""
    assert report["declared_case_counts"]["comparison"]["failed"] == "differs"
    assert "CHECK_PRODUCER_CASE_COUNTS" in report["next_actions"]


def test_output_limit_refuses_without_stdout_prefix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = source_file(tmp_path)
    code = run_cli(
        "audit-deepeval",
            [str(path), "--version", "4.1.1", "--json"],
        audit_deepeval.configure,
        audit_deepeval.produce,
        ReportOutput("deepeval_accounting.v1", max_bytes=1),
    )
    streams = capsys.readouterr()
    assert code == ERROR_EXIT and streams.out == ""
    assert json.loads(streams.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"


def test_human_output_uses_counts_without_interpreting_source_terminal_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = payload()
    body["testCases"][0]["name"] = "\x1b[31muntrusted"
    path = source_file(tmp_path, body)
    assert audit_deepeval.main([str(path)]) == 0
    text = capsys.readouterr().out
    assert "Cases: 3" in text and "Metric occurrences: 4" in text
    assert "evaluator errors 1" in text and "Planned evaluations unknown" in text
    assert "Next:" in text and "\x1b" not in text


def test_missing_file_argument_uses_evidence_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert audit_deepeval.main(["--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and json.loads(streams.err)["code"] == "USAGE_ERROR"


def test_unscored_occurrence_and_absent_case_flag_remain_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = payload()
    first = body["testCases"][0]
    del first["success"]
    del first["metricsData"][0]["score"]
    code, report, _, stderr = invoke(source_file(tmp_path, body), capsys)
    assert code == 0 and stderr == ""
    case = report["cases"][0]
    metric = case["metrics"][0]
    assert case["source_success"] is None
    assert metric["state"] == "unscored"
    assert metric["applied_rule"] == "SCORE_AND_ERROR_ABSENT"
    assert "score" not in metric and "error" not in metric
    assert report["declared_case_counts"]["comparison"] == {
        "passed": "not_comparable",
        "failed": "not_comparable",
    }


def test_packaged_schema_failure_is_not_reported_as_source_content_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from underwrite.instrument.ingest.schema import SchemaConfigurationError

    def fail(*, max_bytes: int, max_depth: int) -> Any:
        del max_bytes, max_depth
        raise SchemaConfigurationError("INVALID_OBSERVATION_SCHEMA")

    monkeypatch.setattr(adapter, "packaged_ingestor", fail)
    code, error, stdout, stderr = invoke(source_file(tmp_path), capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert error["reason"] == "INVALID_OBSERVATION_SCHEMA"
    assert error["location"] == "" and str(tmp_path) not in stderr


def test_numeric_spelling_and_empty_trace_groups_share_domain_results_not_raw_identity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from underwrite_core.canonical import digest_bytes

    reports: list[dict[str, Any]] = []
    for spelling in (1, 1.0):
        body = payload()
        body["testCases"][0]["metricsData"][0].update(score=spelling, threshold=spelling)
        body["traceMetricsScores"] = {
            "agent": {},
            "tool": {},
            "retriever": {},
            "llm": {},
            "base": {},
        }
        path = source_file(tmp_path, body)
        before = path.read_bytes()
        code, report, _, stderr = invoke(path, capsys)
        assert code == 0 and stderr == ""
        assert report["raw_hash"] == digest_bytes(before)
        assert path.read_bytes() == before
        reports.append(report)
    for key in ("cases", "counts", "declared_case_counts", "scope", "next_actions"):
        assert reports[0][key] == reports[1][key]
    assert reports[0]["raw_hash"] != reports[1]["raw_hash"]
    assert reports[0]["observation_digest"] != reports[1]["observation_digest"]
