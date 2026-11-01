"""Legacy root-parser usage failures keep machine output typed and private."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator

ROOT = repo_root(Path(__file__).resolve())
CANARY = "PRIVATE_AGENT_TOKEN"
ERROR_EXIT = 2
DIAGNOSTIC_MAX_BYTES = 1024
ERROR_SCHEMA = Draft202012Validator(
    json.loads((ROOT / "contracts/evidence_error.v1.schema.json").read_bytes())
)


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underwrite.cli.main", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _usage_error(arguments: list[str]) -> None:
    result = _run(*arguments)
    assert result.returncode == ERROR_EXIT
    assert result.stdout == ""
    assert len(result.stderr.encode("utf-8")) <= DIAGNOSTIC_MAX_BYTES
    assert CANARY not in result.stderr
    error: dict[str, Any] = json.loads(result.stderr)
    ERROR_SCHEMA.validate(error)  # pyright: ignore[reportUnknownMemberType]
    assert error == {
        "schema": "evidence_error.v1",
        "code": "USAGE_ERROR",
        "reason": "INVALID_ARGUMENTS_SEE_HELP",
        "location": "",
        "next_action": "CORRECT_ARGUMENTS",
        "retryable": False,
        "exit_code": ERROR_EXIT,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "absence",
            "--root",
            ".",
            "--glob",
            "*.json",
            "--min",
            "1",
            "--unexpected",
            CANARY,
            "--json",
        ],
        ["drift", "check", "--unexpected", CANARY, "--json"],
        ["pins", "refresh", "--repo", "sample", "--unexpected", CANARY, "--json"],
        ["boundary", "scan", ".", "--unexpected", CANARY, "--json"],
    ],
    ids=("absence", "drift-check", "pins-refresh", "boundary-scan"),
)
def test_handlerless_legacy_usage_error_is_typed_atomic_and_private(
    arguments: list[str],
) -> None:
    _usage_error(arguments)


@pytest.mark.parametrize("command", ["drift", "pins", "boundary"])
@pytest.mark.parametrize("action", [None, CANARY], ids=("missing-action", "invalid-action"))
def test_nested_legacy_action_error_is_typed_atomic_and_private(
    command: str, action: str | None
) -> None:
    arguments = [command]
    if action is not None:
        arguments.append(action)
    arguments.append("--json")
    _usage_error(arguments)


def test_unknown_root_command_error_is_typed_atomic_and_private() -> None:
    _usage_error([CANARY, "--json"])


def test_human_legacy_usage_error_is_fixed_bounded_and_private() -> None:
    result = _run("boundary", "scan", ".", "--unexpected", CANARY)
    assert result.returncode == ERROR_EXIT
    assert result.stdout == ""
    assert len(result.stderr.encode("utf-8")) <= DIAGNOSTIC_MAX_BYTES
    assert CANARY not in result.stderr
    prefix = "Evidence command could not complete: "
    assert result.stderr.startswith(prefix)
    error: dict[str, Any] = json.loads(result.stderr.removeprefix(prefix))
    ERROR_SCHEMA.validate(error)  # pyright: ignore[reportUnknownMemberType]
    assert error["code"] == "USAGE_ERROR"
    assert error["reason"] == "INVALID_ARGUMENTS_SEE_HELP"
    assert error["next_action"] == "CORRECT_ARGUMENTS"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--help"],
        ["absence", "--help"],
        ["drift", "--help"],
        ["drift", "check", "--help"],
        ["pins", "--help"],
        ["pins", "refresh", "--help"],
        ["boundary", "--help"],
        ["boundary", "scan", "--help"],
    ],
)
def test_legacy_help_remains_on_stdout(arguments: list[str]) -> None:
    result = _run(*arguments)
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("usage: underwrite")
