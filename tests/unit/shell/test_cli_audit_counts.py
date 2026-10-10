"""Installed-contract Promptfoo accounting without changing ingest or inspect outcomes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _inspection_cases import case, update
from _repo_paths import repo_root
from jsonschema import Draft202012Validator
from underwrite_core.canonical import canonical_bytes, content_digest, digest_bytes

from underwrite.cli import audit_counts
from underwrite.cli.evidence import run_cli
from underwrite.instrument.evidence.accounting import ROW_LIMIT
from underwrite.instrument.ingest import accounting as accounting_adapter
from underwrite.instrument.ingest import inspection
from underwrite.instrument.ingest.accounting import audit_case
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    packaged_ingestor,
)
from underwrite.instrument.ingest.project import Source

ROOT = repo_root(Path(__file__).resolve())
GOLDEN = ROOT / "fixtures/golden/external/promptfoo/results.json"
SOURCE: dict[str, object] = {"format": "promptfoo.eval-output", "format_version": "0.123.0"}
ERROR_EXIT = 2


def _case(tmp_path: Path, body: bytes | None = None) -> Path:
    path = case(tmp_path, GOLDEN.read_bytes() if body is None else body)
    update(path, "source", SOURCE)
    return path


def _invoke(path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any], str, str]:
    code = audit_counts.main([str(path), "--json"])
    streams = capsys.readouterr()
    raw = streams.out if code == 0 else streams.err
    return code, json.loads(raw), streams.out, streams.err


def test_golden_report_is_canonical_and_bound_to_original_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _case(tmp_path)
    before = (path.read_bytes(), (tmp_path / "artifact.json").read_bytes())
    code, report, stdout, stderr = _invoke(path, capsys)
    assert code == 0 and stderr == ""
    schema = json.loads((ROOT / "contracts/promptfoo_accounting.v1.schema.json").read_bytes())
    Draft202012Validator(schema).validate(report)  # pyright: ignore[reportUnknownMemberType]
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["schema"] == "promptfoo_accounting.v1"
    assert report["source"] == SOURCE
    assert report["raw_hash"] == digest_bytes(before[1])
    observation = (
        packaged_ingestor(max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH)
        .file(tmp_path / "artifact.json", Source("promptfoo.eval-output", "0.123.0", None))
        .observation
    )
    assert report["observation_digest"] == content_digest(observation)
    assert (
        report["producer_stats"]
        == report["row_tallies"]
        == {
            "successes": 2,
            "failures": 1,
            "errors": 0,
            "total": 3,
        }
    )
    assert report["deltas"] == {"successes": 0, "failures": 0, "errors": 0, "total": 0}
    assert report["declared_relation"] == "consistent" and report["findings"] == []
    assert report["limitations"] == [
        "PLANNED_DENOMINATOR_UNKNOWN",
        "CAUSE_UNKNOWN",
        "SOURCE_CLAIMS_UNVERIFIED",
        "NO_APPROVAL_AUTHORITY",
    ]
    assert before == (path.read_bytes(), (tmp_path / "artifact.json").read_bytes())


def test_seeded_missing_row_is_a_produced_inconsistent_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = json.loads(GOLDEN.read_bytes())
    payload["results"]["results"].pop()
    path = _case(tmp_path, json.dumps(payload).encode())
    code, report, stdout, stderr = _invoke(path, capsys)
    assert code == 0 and stdout and stderr == ""
    assert report["producer_stats"]["total"] == len(
        json.loads(GOLDEN.read_bytes())["results"]["results"]
    )
    assert report["row_tallies"]["total"] == len(payload["results"]["results"])
    assert report["deltas"] == {"successes": -1, "failures": 0, "errors": 0, "total": -1}
    assert report["declared_relation"] == "inconsistent"
    assert report["findings"] == [
        {
            "code": "CATEGORY_COUNT_MISMATCH",
            "row_index": None,
            "location": "/results/stats/successes",
        }
    ]


@pytest.mark.parametrize(
    ("fault", "expected_code"),
    [
        ("unsupported", "UNSUPPORTED_PROFILE"),
        ("hash", "EXPECTED_HASH_MISMATCH"),
        ("malformed", "INVALID_INPUT"),
        ("missing-stats", "INVALID_INPUT"),
        ("negative-count", "INVALID_INPUT"),
        ("duplicate-keys", "INVALID_INPUT"),
        ("nonfinite", "INVALID_INPUT"),
        ("manifest", "INVALID_MANIFEST"),
        ("path-break", "INVALID_ARTIFACT_PATH"),
        ("missing-file", "SOURCE_READ_FAILED"),
    ],
)
def test_failed_audit_is_typed_and_never_emits_a_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    expected_code: str,
) -> None:
    path = _case(tmp_path)
    if fault == "unsupported":
        update(path, "source", {"format_version": "0.124.0"})
    elif fault == "hash":
        update(path, "artifact", {"expected_hash": "sha256:" + "0" * 64})
    elif fault == "malformed":
        (tmp_path / "artifact.json").write_bytes(b"{")
    elif fault == "duplicate-keys":
        (tmp_path / "artifact.json").write_bytes(b'{"x":1,"x":2}')
    elif fault == "nonfinite":
        (tmp_path / "artifact.json").write_bytes(b'{"x":NaN}')
    elif fault in {"missing-stats", "negative-count"}:
        payload = json.loads(GOLDEN.read_bytes())
        if fault == "missing-stats":
            del payload["results"]["stats"]
        else:
            payload["results"]["stats"]["successes"] = -1
        (tmp_path / "artifact.json").write_text(json.dumps(payload))
    elif fault == "manifest":
        path.write_text("{}")
    elif fault == "path-break":
        update(path, "artifact", {"path": "../escape.json"})
    else:
        (tmp_path / "artifact.json").unlink()
    code, error, stdout, stderr = _invoke(path, capsys)
    assert code == ERROR_EXIT and stdout == "" and stderr
    schema = json.loads((ROOT / "contracts/evidence_error.v1.schema.json").read_bytes())
    Draft202012Validator(schema).validate(error)  # pyright: ignore[reportUnknownMemberType]
    assert error["schema"] == "evidence_error.v1"
    assert error["code"] == expected_code and error["exit_code"] == ERROR_EXIT
    assert "artifact.json" not in stderr


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("malformed", "INVALID_JSON_INPUT"),
        ("duplicate-keys", "DUPLICATE_JSON_KEY"),
        ("nonfinite", "NON_FINITE_JSON_NUMBER"),
        ("missing-stats", "MALFORMED_PROMPTFOO_PAYLOAD"),
    ],
)
def test_audit_preserves_one_trusted_inspection_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    reason: str,
) -> None:
    path = _case(tmp_path)
    artifact = tmp_path / "artifact.json"
    if fault == "malformed":
        artifact.write_bytes(b"{")
    elif fault == "duplicate-keys":
        artifact.write_bytes(b'{"x":1,"x":2}')
    elif fault == "nonfinite":
        artifact.write_bytes(b'{"x":NaN}')
    else:
        payload = json.loads(GOLDEN.read_bytes())
        del payload["results"]["stats"]
        artifact.write_text(json.dumps(payload))
    code, error, stdout, stderr = _invoke(path, capsys)
    assert code == ERROR_EXIT and stdout == "" and stderr
    assert error["code"] == "INVALID_INPUT"
    assert error["reason"] == reason and error["location"] == "/artifact"
    assert "artifact.json" not in stderr


def test_expected_hash_mismatch_keeps_its_existing_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _case(tmp_path)
    update(path, "artifact", {"expected_hash": "sha256:" + "0" * 64})
    code, error, stdout, _ = _invoke(path, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == error["reason"] == "EXPECTED_HASH_MISMATCH"
    assert error["location"] == "/artifact/expected_hash"


@pytest.mark.parametrize(
    "diagnostics",
    [
        [],
        [
            {"code": "INVALID_INPUT", "reason": "DUPLICATE_JSON_KEY", "location": "/artifact"},
            {"code": "INVALID_INPUT", "reason": "INVALID_JSON_INPUT", "location": "/artifact"},
        ],
        [{"code": "INVALID_INPUT", "reason": "RAW_PRIVATE_CONTENT", "location": "/artifact"}],
        [{"code": "INVALID_INPUT", "reason": "DUPLICATE_JSON_KEY", "location": "/source"}],
        [{"code": "UNTRUSTED_CODE", "reason": "DUPLICATE_JSON_KEY", "location": "/artifact"}],
    ],
)
def test_untrusted_or_ambiguous_inspection_diagnostic_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    diagnostics: list[dict[str, str]],
) -> None:
    def inspected(
        path: Path, *, required_source: tuple[str, str] | None = None
    ) -> tuple[dict[str, object], None]:
        return {"expected_hash_check": "not_supplied", "diagnostics": diagnostics}, None

    monkeypatch.setattr(accounting_adapter, "inspect_artifact_with_projection", inspected)
    code, error, stdout, stderr = _invoke(tmp_path / "case.json", capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INVALID_INPUT"
    assert error["reason"] == "INVALID_ARTIFACT"
    assert error["location"] == "/artifact"
    assert "RAW_PRIVATE_CONTENT" not in stderr


@pytest.mark.parametrize(
    ("name", "relation", "reported", "observed"),
    [
        ("complete", "consistent", 3, 3),
        ("count-mismatch", "inconsistent", 3, 2),
        ("category-mismatch", "inconsistent", 3, 3),
        ("observed-zero", "consistent", 1, 1),
        ("provider-error-without-grading", "consistent", 1, 1),
        ("assertion-failure-with-error-text", "consistent", 1, 1),
        ("empty-output-false-none", "consistent", 1, 1),
        ("empty", "consistent", 0, 0),
    ],
)
def test_public_cli_handles_synthetic_task_cards_without_claiming_approval(
    name: str, relation: str, reported: int, observed: int
) -> None:
    manifest = ROOT / "fixtures/examples/evidence" / f"synthetic-promptfoo-{name}.case.json"
    completed = subprocess.run(
        [sys.executable, "-m", "underwrite.cli.main", "audit-counts", str(manifest), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0 and completed.stderr == ""
    report = json.loads(completed.stdout)
    assert report["declared_relation"] == relation
    assert report["producer_stats"]["total"] == reported
    assert report["row_tallies"]["total"] == observed
    assert "PLANNED_DENOMINATOR_UNKNOWN" in report["limitations"]
    assert "NO_APPROVAL_AUTHORITY" in report["limitations"]


def test_adapter_reuses_one_safe_artifact_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _case(tmp_path)
    seen: list[list[str]] = []
    original = inspection.read_at

    def counted(parent: int, parts: list[str], limit: int, flags: tuple[int, int]) -> bytes:
        seen.append(parts)
        return original(parent, parts, limit, flags)

    monkeypatch.setattr(inspection, "read_at", counted)
    assert audit_case(path)["declared_relation"] == "consistent"
    assert seen == [["case.json"], ["artifact.json"]]


def test_row_and_output_limits_are_typed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _case(tmp_path)
    assert (
        run_cli(
            "audit-counts",
            [str(path), "--json"],
            audit_counts.configure,
            audit_counts.produce,
            "promptfoo_accounting.v1",
            output_max_bytes=1,
        )
        == ERROR_EXIT
    )
    streams = capsys.readouterr()
    assert streams.out == "" and json.loads(streams.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"
    payload = json.loads(GOLDEN.read_bytes())
    minimal_row: dict[str, object] = {
        "promptIdx": 0,
        "testIdx": 0,
        "promptId": "local",
        "success": True,
        "score": 0,
        "latencyMs": 0,
        "failureReason": 0,
        "namedScores": {},
    }
    payload["results"]["results"] = [minimal_row for _ in range(ROW_LIMIT + 1)]
    (tmp_path / "artifact.json").write_text(json.dumps(payload))
    code, error, stdout, stderr = _invoke(path, capsys)
    assert code == ERROR_EXIT and stdout == "" and stderr
    assert error["code"] == "COMPUTATION_LIMIT_EXCEEDED"
    assert error["reason"] == "ROW_LIMIT_EXCEEDED"


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_unsafe_artifact_entry_is_refused_without_hang(tmp_path: Path, kind: str) -> None:
    path = _case(tmp_path)
    artifact = tmp_path / "artifact.json"
    artifact.unlink()
    if kind == "symlink":
        artifact.symlink_to(GOLDEN)
    else:
        os.mkfifo(artifact)
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "from underwrite.cli.audit_counts import main; import sys; "
            "sys.exit(main(sys.argv[1:]))",
            str(path),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    )
    assert child.returncode == ERROR_EXIT and child.stdout == ""
    assert json.loads(child.stderr)["code"] == "SOURCE_READ_FAILED"
