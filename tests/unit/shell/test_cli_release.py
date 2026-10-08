import json
from pathlib import Path

import pytest
from _release_cases import release_case
from _release_cases import release_contracts as release_contracts

from underwrite.cli import release
from underwrite.cli.main import main

pytestmark = pytest.mark.usefixtures("release_contracts")
INVOCATION_ERROR = 2


@pytest.mark.parametrize("as_json", [False, True])
def test_release_cli_and_exact_output_limit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    as_json: bool,
) -> None:
    path = release_case(tmp_path)
    args = ["check", str(path)] + (["--json"] if as_json else [])
    assert main(args) == 0
    output = capsys.readouterr()
    assert output.err == ""
    size = len(output.out.encode())
    if as_json:
        assert json.loads(output.out)["result"] == "requirements_matched"
    else:
        assert "Not checked:" in output.out
        assert "Linked evidence:" in output.out
    monkeypatch.setattr(release, "OUTPUT_MAX_BYTES", size)
    assert main(args) == 0
    assert capsys.readouterr().out == output.out
    monkeypatch.setattr(release, "OUTPUT_MAX_BYTES", size - 1)
    assert main(args) == INVOCATION_ERROR
    overflow = capsys.readouterr()
    assert overflow.out == ""
    assert json.loads(overflow.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert len(overflow.err.encode()) <= release.ERROR_MAX_BYTES


def test_errors_and_gaps_have_separate_exit_contracts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = release_case(tmp_path)
    (tmp_path / "eval.json").unlink()
    assert main(["check", str(path), "--json"]) == 1
    report = capsys.readouterr()
    assert json.loads(report.out)["result"] == "gaps_found"
    assert report.err == ""
    assert main(["check", "--unknown-secret"]) == INVOCATION_ERROR
    error = capsys.readouterr()
    assert error.out == ""
    assert "unknown-secret" not in error.err
