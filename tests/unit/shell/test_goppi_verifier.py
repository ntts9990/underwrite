"""Real subprocess mechanics tested with explicit stub executables, never providers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from underwrite.instrument.ingest import goppi_verifier as process

TEST_CLEANUP_DEADLINE = 3


def executable(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "stub-goppi"
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return path


def test_fixed_argv_and_disabled_stdin(tmp_path: Path) -> None:
    program = executable(
        tmp_path,
        "import json, sys\nprint(json.dumps({'argv':sys.argv[1:], 'stdin':sys.stdin.read()}))\n",
    )
    snapshot = tmp_path / "private snapshot; shell syntax"
    raw = process.GoppiCliVerifier(program).verify(snapshot)
    value = json.loads(raw)
    assert value == {
        "argv": ["run", "verify-paired", "--input", str(snapshot), "--json"],
        "stdin": "",
    }


def test_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(process.VerifierError, match="GOPPI_VERIFIER_UNAVAILABLE"):
        process.GoppiCliVerifier(tmp_path / "absent").verify(tmp_path)


def test_default_path_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(process.VerifierError, match="GOPPI_VERIFIER_UNAVAILABLE"):
        process.GoppiCliVerifier().verify(tmp_path)


def test_nonzero_suppresses_private_child_output(tmp_path: Path) -> None:
    program = executable(
        tmp_path, "import sys\nprint('PRIVATE_SECRET',file=sys.stderr)\nsys.exit(7)\n"
    )
    with pytest.raises(process.VerifierError, match="GOPPI_VERIFIER_REFUSED") as error:
        process.GoppiCliVerifier(program).verify(tmp_path)
    assert "PRIVATE_SECRET" not in str(error.value)
    assert str(program) not in str(error.value)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_concurrent_pipe_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str) -> None:
    monkeypatch.setattr(process, "STDOUT_MAX_BYTES", 1024)
    monkeypatch.setattr(process, "STDERR_MAX_BYTES", 1024)
    program = executable(
        tmp_path,
        f"import sys\nsys.{stream}.write('PRIVATE_SECRET' * 100000)\nsys.{stream}.flush()\n",
    )
    with pytest.raises(process.VerifierError, match="GOPPI_VERIFIER_OUTPUT_LIMIT"):
        process.GoppiCliVerifier(program).verify(tmp_path)


@pytest.mark.parametrize(
    "body",
    [
        "import time\ntime.sleep(20)\n",
        # The leader has exited, but a descendant keeps both pipes open.
        "import os,time\nif os.fork()==0: time.sleep(20)\nelse: os._exit(0)\n",
        # No pipe remains but the process is still running.
        "import os,time\nos.close(1)\nos.close(2)\ntime.sleep(20)\n",
    ],
)
def test_deadline_includes_descendant_pipes_and_reaping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    monkeypatch.setattr(process, "DEADLINE_SECONDS", 0.15)
    program = executable(tmp_path, body)
    start = time.monotonic()
    with pytest.raises(process.VerifierError, match="GOPPI_VERIFIER_TIMEOUT"):
        process.GoppiCliVerifier(program).verify(tmp_path)
    assert time.monotonic() - start < TEST_CLEANUP_DEADLINE


def test_exact_limits_are_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process, "STDOUT_MAX_BYTES", 4)
    monkeypatch.setattr(process, "STDERR_MAX_BYTES", 4)
    program = executable(tmp_path, "import os\nos.write(1,b'abcd')\nos.write(2,b'efgh')\n")
    assert process.GoppiCliVerifier(program).verify(tmp_path) == b"abcd"


def test_unsupported_platform_is_finite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "unsupported")
    with pytest.raises(process.VerifierError, match="GOPPI_VERIFIER_PLATFORM_UNSUPPORTED"):
        process.GoppiCliVerifier().verify(tmp_path)


def test_interruption_reaps_child_and_closes_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    children: list[subprocess.Popen[bytes]] = []
    interrupted = KeyboardInterrupt()

    def interrupt(child: subprocess.Popen[bytes], deadline: float) -> bytes:
        children.append(child)
        raise interrupted

    monkeypatch.setattr(process, "_receive", interrupt)
    program = executable(tmp_path, "import time\ntime.sleep(20)\n")
    with pytest.raises(KeyboardInterrupt) as error:
        process.GoppiCliVerifier(program).verify(tmp_path)
    assert error.value is interrupted
    assert len(children) == 1
    child = children[0]
    assert child.poll() is not None
    assert child.stdout is not None and child.stdout.closed
    assert child.stderr is not None and child.stderr.closed
