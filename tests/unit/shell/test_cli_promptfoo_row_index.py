"""Promptfoo's selected ordinal view omits source content on both CLI paths."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import digest_bytes

from underwrite.cli import audit_promptfoo, main
from underwrite.instrument.ingest import accounting as adapter

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "fixtures/golden/external"
VERSIONED = GOLDEN / "promptfoo-0.124.1/results.json"
ERROR_EXIT = 2


def _invoke(
    path: Path, capsys: pytest.CaptureFixture[str], *, version: str = "0.124.1"
) -> tuple[int, str, str]:
    code = main.main(
        ["audit-promptfoo", str(path), "--version", version, "--view", "row-index", "--json"]
    )
    streams = capsys.readouterr()
    return code, streams.out, streams.err


def test_pinned_rows_and_default_json_are_stable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["audit-promptfoo", str(VERSIONED), "--version", "0.124.1", "--json"]) == 0
    default = capsys.readouterr()
    assert default.err == ""
    assert hashlib.sha256(default.out.encode()).hexdigest() == (
        "8d5b50bbdc0f4eb448e4e6360eefc34f36e6808cdeb09e85ce0fdd8ba4e4cc06"
    )

    code, stdout, stderr = _invoke(VERSIONED, capsys)
    assert code == 0 and stderr == ""
    result = json.loads(stdout)
    assert result == adapter.audit_promptfoo_row_index_file(VERSIONED, version="0.124.1")
    assert result["schema"] == "promptfoo_row_index.v1"
    assert result["raw_hash"] == digest_bytes(VERSIONED.read_bytes())
    assert [
        (
            row["row_index"],
            row["category"],
            row["source_success"],
            row["source_failure_reason"],
            row["grading_pass"],
        )
        for row in result["rows"]
    ] == [
        (0, "successes", True, 0, True),
        (1, "failures", False, 1, False),
        (2, "errors", False, 2, None),
        (3, "errors", False, 2, None),
        (4, "successes", True, 0, True),
        (5, "errors", False, 2, None),
        (6, "successes", True, 0, True),
    ]
    assert result["row_tallies"] == {"successes": 3, "failures": 1, "errors": 3, "total": 7}
    assert result["scope"]["values"] == "withheld" and result["verification"] == []
    assert "score" not in result["rows"][0] and "cost" not in result["rows"][0]


@pytest.mark.parametrize(
    "version,folder", [("0.123.0", "promptfoo"), ("0.124.1", "promptfoo-0.124.1")]
)
def test_both_selected_versions_have_importable_entrypoint(
    capsys: pytest.CaptureFixture[str], version: str, folder: str
) -> None:
    path = GOLDEN / folder / "results.json"
    code, stdout, stderr = _invoke(path, capsys, version=version)
    assert code == 0 and stderr == ""
    assert json.loads(stdout) == adapter.audit_promptfoo_row_index_file(path, version=version)


def test_hostile_source_content_and_path_do_not_escape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "PRIVATE_PROMPTFOO_CANARY"
    folder = tmp_path / canary
    folder.mkdir()
    path = folder / "source.json"
    source = cast(dict[str, Any], json.loads(VERSIONED.read_bytes()))
    rows = cast(list[dict[str, Any]], source["results"]["results"])
    for row in rows:
        row["id"] = canary
        row["promptId"] = canary
        row["traceId"] = canary
        row["provider"] = {"id": canary, "label": canary}
        row["prompt"] = {"raw": canary, "label": canary}
        row["testCase"] = {"description": canary, "vars": {"secret": canary}}
        row["vars"] = {"secret": canary}
        row["response"] = {"output": canary, "error": canary, "stack": canary}
        row["error"] = canary
        if isinstance(row.get("gradingResult"), dict):
            row["gradingResult"]["reason"] = canary
            row["gradingResult"]["comment"] = canary
    rows[0]["score"] = 0
    rows[4]["score"] = 0.3
    rows[0].pop("cost", None)
    path.write_text(json.dumps(source), encoding="utf-8")

    code, stdout, stderr = _invoke(path, capsys)
    assert code == 0 and stderr == "" and canary not in stdout
    result = json.loads(stdout)
    assert result["rows"][0]["category"] == result["rows"][4]["category"] == "successes"
    assert result["rows"][0]["source_success"] is True
    assert all("score" not in row and "cost" not in row for row in result["rows"])

    assert audit_promptfoo.main([str(path), "--version", "0.124.1", "--view", "row-index"]) == 0
    human = capsys.readouterr()
    assert human.err == "" and canary not in human.out

    del rows[0]["success"]
    path.write_text(json.dumps(source), encoding="utf-8")
    code, stdout, stderr = _invoke(path, capsys)
    assert code == ERROR_EXIT and stdout == "" and canary not in stderr
    assert json.loads(stderr)["schema"] == "evidence_error.v1"


def test_selected_view_reads_file_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "source.json"
    path.write_bytes(VERSIONED.read_bytes())
    original = adapter.read_local_file
    reads: list[Path] = []

    def traced(selected: Path, maximum: int) -> bytes:
        reads.append(selected)
        return original(selected, maximum)

    monkeypatch.setattr(adapter, "read_local_file", traced)
    adapter.audit_promptfoo_row_index_file(path, version="0.124.1")
    assert reads == [path]


def test_row_index_view_rejects_version_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(path: Path) -> bytes:
        del path
        pytest.fail("file read before version selection")

    monkeypatch.setattr(adapter, "read_promptfoo_file", forbidden)
    assert audit_promptfoo.main(
        [
            str(tmp_path / "missing"),
            "--view",
            "row-index",
            "--json",
            "--version",
            "private-version",
        ]
    ) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and "private-version" not in streams.err


def test_row_index_api_projection_fault_has_finite_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(raw: bytes, *, version: str, row_index: bool) -> dict[str, object]:
        del raw, version, row_index
        raise RuntimeError("PRIVATE_PROMPTFOO_CANARY")

    monkeypatch.setattr(adapter, "_audit_promptfoo_bytes", fail)
    with pytest.raises(adapter.AccountingCaseError) as caught:
        adapter.audit_promptfoo_row_index_file(VERSIONED, version="0.124.1")
    assert caught.value.code == "INTERNAL_ERROR"
    assert caught.value.reason == "PROMPTFOO_ROW_INDEX_INVALID"
    assert "PRIVATE_PROMPTFOO_CANARY" not in str(caught.value)
