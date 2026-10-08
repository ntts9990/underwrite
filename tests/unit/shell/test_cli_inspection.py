"""CLI-only stream/parser/cancellation behavior stays outside library mutation tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _inspection_cases import case, update
from underwrite_core.canonical import canonical_bytes

from underwrite.cli import inspection as cli
from underwrite.instrument.ingest.inspection import InspectionError, inspect_artifact

INVOCATION_EXIT = 2


@pytest.mark.parametrize(
    ("outcome", "headline", "action"),
    [
        ("created", "Observation created", "No artifact correction indicated"),
        ("mismatch", "Observation not created", "Check the intended artifact"),
        ("invalid", "Inspection could not complete", "Correct the case manifest"),
    ],
)
def test_human_outcome_and_action_precede_identity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
    headline: str,
    action: str,
) -> None:
    path = case(
        tmp_path,
        (
            b'{"schema_version":"underwrite.evidence-bundle.v1",'
            b'"manifest":{"run_id":"example"},'
            b'"sources":[],"readings":[],"availability":[]}'
        ),
    )
    update(path, "source", {"format": "underwrite.evidence-bundle", "format_version": "v1"})
    if outcome == "mismatch":
        update(path, "artifact", {"expected_hash": "sha256:" + "0" * 64})
    elif outcome == "invalid":
        path.write_text("{}")
    cli.main([str(path)])
    captured = capsys.readouterr()
    text = captured.err if outcome == "invalid" else captured.out
    assert text.startswith(headline)
    labels = ["Checked:", "Gap:", "Not checked:", "Next action:", "Details:"]
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions)
    assert action in text[: text.index("Details:")]
    if outcome == "created":
        assert "no expected-hash comparison performed" in text
    if outcome == "mismatch":
        assert "/artifact/expected_hash" in text[: text.index("Details:")]
    if outcome != "invalid":
        assert text.index("Next action:") < text.index("manifest_hash:")


def test_human_all_values_are_escaped_and_bounded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    unsafe = "\x1b\n" + "x" * (cli.DISPLAY_LIMIT * 2)

    def failure(path: Path) -> dict[str, object]:
        raise InspectionError(unsafe, unsafe, "", unsafe)

    monkeypatch.setattr(cli, "inspect_artifact", failure)
    assert cli.main(["case.json"]) == INVOCATION_EXIT
    captured = capsys.readouterr()
    assert captured.out == ""
    text = captured.err
    assert "\x1b" not in text
    assert "\\u001b\\n" in text
    assert "[truncated]" in text
    assert "x" * (cli.DISPLAY_LIMIT + 1) not in text


@pytest.mark.parametrize("outcome", ["created", "malformed", "mismatch", "invalid-source"])
@pytest.mark.parametrize("as_json", [False, True])
def test_cli_stream_and_exit_match_library_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
    as_json: bool,
) -> None:
    path = case(
        tmp_path,
        (
            b'{"schema_version":"underwrite.evidence-bundle.v1",'
            b'"manifest":{"run_id":"example"},'
            b'"sources":[],"readings":[],"availability":[]}'
        ),
    )
    update(path, "source", {"format": "underwrite.evidence-bundle", "format_version": "v1"})
    if outcome == "malformed":
        (tmp_path / "artifact.json").write_bytes(b"{")
    elif outcome == "mismatch":
        update(path, "artifact", {"expected_hash": "sha256:" + "0" * 64})
    elif outcome == "invalid-source":
        update(path, "source", {"ref": " "})
    try:
        expected = inspect_artifact(path)
        expected_exit = 0 if expected["projection"] == "created" else 1
    except InspectionError as exc:
        expected = exc.payload
        expected_exit = INVOCATION_EXIT
    assert cli.main([str(path), *(["--json"] if as_json else [])]) == expected_exit
    captured = capsys.readouterr()
    if expected_exit == INVOCATION_EXIT:
        assert captured.out == "" and captured.err
        actual = captured.err
    else:
        assert captured.err == "" and captured.out
        actual = captured.out
    if as_json:
        assert actual == canonical_bytes(expected).decode() + "\n"
    else:
        assert "no release or deployment decision" in actual
        for key, value in expected.items():
            assert f"{key}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}" in actual


def invoke(path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = cli.main([str(path), "--json"])
    captured = capsys.readouterr()
    assert not (captured.out and captured.err)
    return code, json.loads(captured.err if code == INVOCATION_EXIT else captured.out)


@pytest.mark.parametrize("target", ["manifest", "artifact"])
def test_writerless_fifo_rejection_is_bounded(tmp_path: Path, target: str) -> None:
    path = case(tmp_path)
    fifo = path if target == "manifest" else tmp_path / "artifact.json"
    fifo.unlink()
    os.mkfifo(fifo)
    command = [sys.executable, "-m", "underwrite.cli.main", "inspect", str(path), "--json"]
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = child.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        pytest.fail("Owned inspection FIFO child exceeded deadline; killed and reaped")
    assert child.returncode == INVOCATION_EXIT and stdout == ""
    error = json.loads(stderr)
    assert error["code"] == "SOURCE_READ_FAILED"
    assert error["reason"] == "SOURCE_NOT_REGULAR_FILE"


def test_unknown_failure_is_not_an_artifact_finding(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def defect(path: Path) -> dict[str, object]:
        raise ValueError("secret bug\x1b")

    monkeypatch.setattr(cli, "inspect_artifact", defect)
    code, error = invoke(case(tmp_path), capsys)
    assert code == INVOCATION_EXIT and error["code"] == "INTERNAL_ERROR"
    assert "secret" not in json.dumps(error)


def test_cancellation_is_not_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def cancel(path: Path) -> dict[str, object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "inspect_artifact", cancel)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["case.json"])


@pytest.mark.parametrize("args", [[], ["--other", "secret\x1b"], ["a", "b"]])
def test_parser_failure_does_not_echo_arguments(
    args: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([*args, "--json"]) == INVOCATION_EXIT
    captured = capsys.readouterr()
    assert captured.out == "" and "secret" not in captured.err
    assert json.loads(captured.err)["code"] == "USAGE_ERROR"
