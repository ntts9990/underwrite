"""The wheel smoke gate fails visibly when installation inputs or CLI output drift."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import smoke_wheels


def test_missing_wheel_fails_before_install(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert smoke_wheels.main(["--dist", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "expected one underwrite_core-*.whl" in captured.err


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "failure"),
    [
        (1, "", "CLI failed", "command failed"),
        (0, "not JSON", "", "invalid CLI JSON"),
        (0, "[]", "", "CLI JSON is not an object"),
    ],
)
def test_cli_output_failures_are_not_written_as_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    stderr: str,
    failure: str,
) -> None:
    def response(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    monkeypatch.setattr(smoke_wheels, "_command", response)
    output = tmp_path / "observation.json"
    with pytest.raises(smoke_wheels.SmokeFailure, match=failure):
        smoke_wheels._cli_json(  # pyright: ignore[reportPrivateUsage]
            tmp_path / "underwrite", ["ingest"], output, tmp_path
        )
    assert not output.exists()
