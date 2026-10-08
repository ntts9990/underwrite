"""Real owned Unix-file regressions; every potentially blocking child is bounded."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _repo_paths import repo_root

ROOT = repo_root(Path(__file__).resolve())
CLI = ROOT / ".venv/bin/underwrite"
RAW = (
    b'{"schema_version":"underwrite.evidence-bundle.v1",'
    b'"manifest":{"run_id":"local"},'
    b'"sources":[],"readings":[],"availability":[]}'
)
UNAVAILABLE_EXIT = 2


def _arguments(command: str, route: str, path: Path) -> list[str]:
    args = [
        command,
        "--format",
        "underwrite.evidence-bundle",
        "--version",
        "v1",
        route,
        str(path),
        "--json",
    ]
    if route == "--http-body-file":
        args += ["--transport-locator", "synthetic:not-fetched"]
    return args


def _bounded(args: list[str]) -> subprocess.CompletedProcess[str]:
    child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = child.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        child.kill()
        stdout, stderr = child.communicate()
        pytest.fail(f"owned child {child.pid} exceeded deadline; killed/reaped: {stderr!r}")
    return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)


def _unavailable(result: subprocess.CompletedProcess[str], command: str) -> None:
    assert result.returncode == UNAVAILABLE_EXIT, result.stderr
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "schema": "instrument_error.v1",
        "command": command,
        "code": "SOURCE_READ_FAILED",
        "reason": "SOURCE_NOT_REGULAR_FILE",
        "exit_code": 2,
    }


@pytest.mark.parametrize("command", ["ingest", "project"])
@pytest.mark.parametrize("route", ["--file", "--http-body-file"])
@pytest.mark.parametrize("symlink", [False, True])
def test_writerless_fifo_rejects_without_waiting(
    tmp_path: Path, command: str, route: str, symlink: bool
) -> None:
    fifo = tmp_path / "owned-fifo"
    os.mkfifo(fifo)
    path = fifo
    if symlink:
        path = tmp_path / "fifo-link"
        path.symlink_to(fifo)
    _unavailable(_bounded([str(CLI), *_arguments(command, route, path)]), command)


@pytest.mark.parametrize("command", ["ingest", "project"])
@pytest.mark.parametrize("route", ["--file", "--http-body-file"])
def test_unsupported_format_rejects_before_opening_fifo(
    tmp_path: Path, command: str, route: str
) -> None:
    path = tmp_path / "owned-fifo"
    os.mkfifo(path)
    args = _arguments(command, route, path)
    args[args.index("--version") + 1] = "unsupported-version"
    result = _bounded([str(CLI), *args])
    assert result.returncode == 1 and result.stdout == ""
    assert json.loads(result.stderr)["reason"] == "UNSUPPORTED_FORMAT"


@pytest.mark.parametrize("command", ["ingest", "project"])
@pytest.mark.parametrize("route", ["--file", "--http-body-file"])
@pytest.mark.parametrize("limit", [sys.maxsize - 1, sys.maxsize, sys.maxsize + 1, sys.maxsize**2])
def test_tiny_regular_input_accepts_full_large_explicit_budget(
    tmp_path: Path, command: str, route: str, limit: int
) -> None:
    path = tmp_path / "tiny.json"
    path.write_bytes(RAW)
    args = [str(CLI), *_arguments(command, route, path)]
    ordinary = _bounded(args)
    large = _bounded([*args, "--max-bytes", str(limit)])
    assert ordinary.returncode == large.returncode == 0, large.stderr
    assert ordinary.stderr == large.stderr == ""
    assert large.stdout == ordinary.stdout


@pytest.mark.parametrize("route", ["--file", "--http-body-file"])
def test_path_swap_immediately_before_open_still_rejects_fifo(tmp_path: Path, route: str) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(RAW)
    fifo = tmp_path / "owned-fifo"
    os.mkfifo(fifo)
    # A deterministic replacement, not a scheduler-dependent race. Only owned paths.
    script = """
import os, sys
from pathlib import Path
from underwrite.cli.main import main
target, fifo = Path(sys.argv[1]), Path(sys.argv[2])
original = os.open
def swap(path, flags, *args, **kwargs):
    if Path(path) == target:
        os.replace(fifo, target)
    return original(path, flags, *args, **kwargs)
os.open = swap
sys.argv = ['underwrite', *sys.argv[3:]]
raise SystemExit(main())
"""
    result = _bounded(
        [sys.executable, "-c", script, str(path), str(fifo), *_arguments("ingest", route, path)]
    )
    _unavailable(result, "ingest")


@pytest.mark.parametrize("command", ["ingest", "project"])
@pytest.mark.parametrize("route", ["--file", "--http-body-file"])
def test_larger_than_default_file_is_accepted_only_with_sufficient_budget(
    tmp_path: Path, command: str, route: str
) -> None:
    from underwrite.cli.instrument import DEFAULT_MAX_BYTES

    path = tmp_path / "large.json"
    raw = RAW + b" " * DEFAULT_MAX_BYTES
    path.write_bytes(raw)
    args = [str(CLI), *_arguments(command, route, path)]
    accepted = _bounded([*args, "--max-bytes", str(len(raw))])
    assert accepted.returncode == 0, accepted.stderr
    rejected = _bounded([*args, "--max-bytes", str(len(raw) - 1)])
    assert rejected.returncode == 1 and rejected.stdout == ""
    assert json.loads(rejected.stderr)["reason"] == "BYTE_LIMIT_EXCEEDED"
