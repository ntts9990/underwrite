"""The review context is digest-bound without replacing paired measurements."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from underwrite_core.canonical import content_digest

from underwrite.cli.accept import main as accept_main
from underwrite.cli.main import main
from underwrite.instrument.evidence.paired_binary import evaluate_paired_binary

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "fixtures/examples/evidence"
ERROR_EXIT = 2


def documents() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        json.loads((EXAMPLES / "synthetic-paired-complete.json").read_text()),
        json.loads((EXAMPLES / "paired-binary-policy.json").read_text()),
        json.loads((EXAMPLES / "synthetic-review-context.json").read_text()),
    )


def invoke(
    tmp_path: Path, capsys: Any, *, context: dict[str, Any] | None = None, incomplete: bool = False
) -> tuple[int, dict[str, Any], str]:
    data, policy, default_context = documents()
    context = default_context if context is None else context
    if incomplete:
        data["slot_records"] = [
            row
            for row in data["slot_records"]
            if not (row["case_id"] == "case-a" and row["arm"] == "candidate" and row["repeat"] == 0)
        ]
        context["binding"]["input_digest"] = content_digest(data)
    args = ["review-pair", "--json"]
    for flag, document in (("input", data), ("policy", policy), ("context", context)):
        path = tmp_path / f"{flag}.json"
        path.write_text(json.dumps(document))
        args.extend([f"--{flag}", str(path)])
    code = main(args)
    streams = capsys.readouterr()
    return (
        code,
        json.loads(streams.out if code == 0 else streams.err),
        (streams.err if code == 0 else streams.out),
    )


def test_cli_references_exact_existing_report_and_all_condition_states(
    tmp_path: Path, capsys: Any
) -> None:
    code, report, other = invoke(tmp_path, capsys)
    assert code == 0 and other == ""
    data, policy, context = documents()
    evidence = evaluate_paired_binary(data, policy)
    assert report["schema"] == "paired_review.v1"
    assert report["paired_evidence_ref"]["digest"] == content_digest(evidence)
    assert report["paired_evidence_ref"]["status"] == evidence["status"]
    assert report["paired_evidence_ref"]["reason"] == evidence["reason"]
    assert report["context_digest"] == content_digest(context)
    assert {entry["classification"] for entry in report["conditions"]["entries"]} == {
        "intended_change",
        "invariant_changed",
        "unknown",
    }
    assert report["actual_conditions_verified"] is False
    assert report["declarations_verified"] is False


@pytest.mark.parametrize("field", ["input_digest", "policy_digest"])
def test_either_binding_mismatch_is_atomic(tmp_path: Path, capsys: Any, field: str) -> None:
    _, _, context = documents()
    context["binding"][field] = "sha256:" + "0" * 64
    code, error, other = invoke(tmp_path, capsys, context=context)
    assert code == ERROR_EXIT and other == ""
    assert error["code"] == "INPUT_BINDING_MISMATCH"
    assert error["schema"] == "evidence_error.v1"


def test_incomplete_evidence_remains_not_measured(tmp_path: Path, capsys: Any) -> None:
    code, report, other = invoke(tmp_path, capsys, incomplete=True)
    assert code == 0 and other == ""
    assert report["paired_evidence_ref"]["status"] == "not_measured"
    assert report["paired_evidence_ref"]["reason"] == "PRIMARY_COHORT_INCOMPLETE"


def test_condition_edit_is_a_new_context_not_binding_failure(tmp_path: Path, capsys: Any) -> None:
    _, first, _ = invoke(tmp_path, capsys)
    _, _, context = documents()
    context["conditions"][0]["candidate"] = "different declared prompt"
    code, second, _ = invoke(tmp_path, capsys, context=context)
    assert code == 0
    assert first["context_digest"] != second["context_digest"]
    assert first["paired_evidence_ref"] == second["paired_evidence_ref"]


def test_context_duplicate_json_keys_are_rejected_without_echo(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "context.json"
    path.write_text('{"schema":"paired_review_context.v1","schema":"SENTINEL"}')
    code = main(
        [
            "review-pair",
            "--input",
            str(EXAMPLES / "synthetic-paired-complete.json"),
            "--policy",
            str(EXAMPLES / "paired-binary-policy.json"),
            "--context",
            str(path),
            "--json",
        ]
    )
    streams = capsys.readouterr()
    assert code == ERROR_EXIT and streams.out == ""
    assert "SENTINEL" not in streams.err
    assert json.loads(streams.err)["reason"] == "INVALID_JSON_INPUT"


def test_review_is_not_a_native_read(tmp_path: Path, capsys: Any) -> None:
    _, report, _ = invoke(tmp_path, capsys)
    path = tmp_path / "review.json"
    path.write_text(json.dumps(report))
    fixtures = ROOT / "fixtures/examples/acceptance"
    code = accept_main(
        [
            "--candidate",
            str(fixtures / "candidate.json"),
            "--claims",
            str(fixtures / "claims.json"),
            "--read",
            str(path),
            "--json",
        ]
    )
    streams = capsys.readouterr()
    assert code == ERROR_EXIT and streams.out == ""
    assert json.loads(streams.err)["code"] == "INVALID_READ"


@pytest.mark.parametrize("kind", ["oversized", "directory"])
def test_context_reader_keeps_existing_file_boundaries(
    tmp_path: Path, capsys: Any, kind: str
) -> None:
    path = tmp_path / "context.json"
    if kind == "oversized":
        path.write_text(" " * (64 * 1024 + 1))
    else:
        path.mkdir()
    code = main(
        [
            "review-pair",
            "--input",
            str(EXAMPLES / "synthetic-paired-complete.json"),
            "--policy",
            str(EXAMPLES / "paired-binary-policy.json"),
            "--context",
            str(path),
            "--json",
        ]
    )
    streams = capsys.readouterr()
    assert code == ERROR_EXIT and streams.out == ""
    assert json.loads(streams.err)["schema"] == "evidence_error.v1"


def test_review_help_explains_declared_boundary(capsys: Any) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["review-pair", "--help"])
    assert exc.value.code == 0
    assert "declarations do not verify" in " ".join(capsys.readouterr().out.split())
